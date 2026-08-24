#!/usr/bin/env python3
"""Generate GemFire OpenRewrite mapping files from Broadcom Artifactory.

Reads the ``com.vmware.gemfire`` artifact catalog on Broadcom Artifactory,
detects ``(prefix, generation token, GemFire version)`` families, and
(re)generates the ``.advisor/mappings/*.json`` files for spring-boot /
spring-data / spring-session / spring-integration GemFire, plus the generic
``gemfire-10-x.json``.

spring-integration is a special case: its artifactId's generation token is the
*Spring Integration* project's own version (e.g. "6.5"), not the Spring Boot
generation the other three prefixes use. Its real Spring Boot generation is
read from its POM's dependency on the matching spring-data-{SB}-gemfire-{GF}
coordinate -- see resolve_family_boot_gen().

The generator is *authoritative*: it recomputes the full desired state from
Artifactory on every run, so it is idempotent (same Artifactory state produces
byte-identical output) and naturally picks up new versions / new hops as they
are published.

Hop chains are wired *within a single GemFire line only* — each GemFire line
walks its Spring Boot generations (e.g. 10.1: 2.7 -> 3.1 -> 3.3 -> 3.5) and is
terminal at the highest Spring Boot generation that line supports. There is no
automatic cross-GemFire jump driven by a Spring Boot upgrade.

Separately, ``gemfire-10-x.json`` (the generic native-artifact mapping) carries
a *reactive* GemFire-line realignment hop: it is triggered by the native
``gemfire-core``/etc. coordinate's own version, not by a Spring Boot bump. If a
project has already moved its native GemFire artifacts to a new line (e.g. a
manual ``GEMFIRE_VERSION`` bump to 10.2.x) while its spring-boot/spring-data/
spring-session bridge coordinates are still named for an older line (-10.1),
this hop rewrites the bridge coordinates to match, for whichever Spring Boot
generation both lines publish. It never initiates the native-artifact jump
itself — only reacts once the project has already made it. See
wire_gf_realignment().

Usage:
    python3 scripts/generate_gemfire_mappings.py [options]

Options:
    --mappings-dir DIR      Target mappings directory (default: repo .advisor/mappings)
    --prefixes LIST         Comma-separated subset of spring-boot,spring-data,spring-session
    --min-gf VER            Minimum GemFire line to generate (default: 10.1)
    --artifactory-root URL  Artifactory root (…/artifactory); overrides env/default
    --artifactory-repo KEY  Artifactory repository key holding GemFire artifacts
    --dry-run               Print planned writes without modifying files
    --verbose               Log family / version discovery
    --delete-orphans        Delete mapping files with no matching Artifactory family
                            (dry-run prints what would be deleted without removing)

Auth: set GEMFIRE_ARTIFACTORY_USERNAME / GEMFIRE_ARTIFACTORY_PASSWORD, or fall
back to ~/.gradle/gradle.properties (gemfireRepoUsername / gemfireRepoPassword).

Endpoint: defaults to the public Broadcom Artifactory. Override for an internal
mirror via GEMFIRE_ARTIFACTORY_ROOT (…/artifactory) and GEMFIRE_ARTIFACTORY_REPO
(repository key), or the matching --artifactory-root / --artifactory-repo flags.
Precedence: CLI flag > env var > default.
"""

import argparse
import base64
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path

# --- Artifactory endpoints --------------------------------------------------

# The GemFire artifacts live at, on the public Broadcom instance:
#   files:   {root}/{repo}/com/vmware/gemfire/...
#   listing: {root}/api/storage/{repo}/com/vmware/gemfire/...   (JFrog storage API)
# Both the root and the repo key are overridable (env or CLI) so this can point
# at an internal Artifactory mirror without code changes.
DEFAULT_ARTIFACTORY_ROOT = "https://packages.broadcom.com/artifactory"
DEFAULT_ARTIFACTORY_REPO = "gemfire"
GROUP_ID = "com.vmware.gemfire"
GROUP_PATH = GROUP_ID.replace(".", "/")


def resolve_endpoints(root, repo):
    """Build the (files_url, storage_url) pair from a root + repo key."""
    root = root.rstrip("/")
    files_url = f"{root}/{repo}/{GROUP_PATH}"
    storage_url = f"{root}/api/storage/{repo}/{GROUP_PATH}"
    return files_url, storage_url

# --- Policy constants (edit here) -------------------------------------------

# Artifact-name prefixes that identify each mapping family. Note that
# spring-boot-actuator-*, spring-boot-logging-* and spring-boot-session-* are
# all *spring-boot* artifacts (they start with "spring-boot-").
#
# spring-integration is the odd one out: its artifactId encodes the *Spring
# Integration* project's own version (e.g. "6.5", "7.1"), not the Spring Boot
# generation the way boot/data/session do. See resolve_family_boot_gen().
PREFIXES = ("spring-boot", "spring-data", "spring-session", "spring-integration")

# Prefixes whose artifactId generation token already *is* the Spring Boot
# generation. Anything else (spring-integration) needs its Spring Boot
# generation read from its POM instead of its artifact name.
BOOT_GEN_FROM_NAME_PREFIXES = ("spring-boot", "spring-data", "spring-session")

# Java requirement is read from the artifact's compiled bytecode (the real
# minimum). The map below is only a fallback when a jar cannot be read, and the
# generic-mapping default likewise. Class-file major version -> Java feature ver.
CLASS_MAGIC = b"\xca\xfe\xba\xbe"
JAVA_BY_CLASS_MAJOR = {
    52: 8, 53: 9, 54: 10, 55: 11, 56: 12, 57: 13, 58: 14, 59: 15, 60: 16,
    61: 17, 62: 18, 63: 19, 64: 20, 65: 21, 66: 22, 67: 23, 68: 24, 69: 25,
}
# Fallback Java baseline per Spring Boot major generation (offline / unreadable).
FALLBACK_JAVA_BY_SB_MAJOR = {2: 8, 3: 17, 4: 17}
DEFAULT_JAVA = 17

# Only generate mappings for this GemFire line and newer.
DEFAULT_MIN_GF = "10.1"

# Coordinate-swap recipe (build-tool-agnostic; used by Broadcom built-ins).
CHANGE_DEPENDENCY_RECIPE = "org.openrewrite.java.dependencies.ChangeDependency"

# Generic core-gemfire mapping is version-tracking only (same artifactId across
# versions), so its blocks stay terminal with no recipes.
GENERIC_MAPPING_FILE = "gemfire-10-x.json"
GENERIC_MAPPING_JAVA = 8  # fallback only; real value read from gemfire-core bytecode


# --- Version helpers --------------------------------------------------------

def parse_version(text):
    """Parse a dotted version into a tuple of ints, ignoring trailing junk."""
    parts = []
    for token in text.split("."):
        m = re.match(r"\d+", token)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def block_key(version):
    """Collapse a concrete version to its major.minor.x block key."""
    t = parse_version(version)
    major = t[0] if len(t) > 0 else 0
    minor = t[1] if len(t) > 1 else 0
    return f"{major}.{minor}.x"


def sb_tuple(sb_gen):
    """Sort key for a Spring Boot generation like '3.5' -> (3, 5)."""
    return parse_version(sb_gen)


# --- HTTP -------------------------------------------------------------------

class Artifactory:
    """Thin authenticated client for the Broadcom Artifactory GemFire repo."""

    def __init__(self, username, password, files_url, storage_url):
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._auth = f"Basic {token}"
        self.files_url = files_url
        self.storage_url = storage_url

    def _get(self, url):
        req = urllib.request.Request(url, headers={"Authorization": self._auth})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    def list_artifacts(self):
        """Return the folder names directly under com/vmware/gemfire/."""
        data = json.loads(self._get(f"{self.storage_url}/"))
        return sorted(
            child["uri"].strip("/")
            for child in data.get("children", [])
            if child.get("folder")
        )

    def versions(self, artifact):
        """Return (all_versions, release) from an artifact's maven-metadata.

        Returns ([], None) if the artifact has no metadata (e.g. 404).
        """
        url = f"{self.files_url}/{artifact}/maven-metadata.xml"
        try:
            raw = self._get(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return [], None
            raise
        root = ET.fromstring(raw)
        versioning = root.find("versioning")
        if versioning is None:
            return [], None
        all_versions = [v.text for v in versioning.findall(".//version") if v.text]
        release_el = versioning.find("release")
        release = release_el.text if release_el is not None else None
        if release is None and all_versions:
            release = max(all_versions, key=parse_version)
        # De-duplicate while keeping a stable, version-sorted order.
        uniq = sorted(set(all_versions), key=parse_version)
        return uniq, release

    def pom(self, artifact, version):
        """Return an artifact's POM text, or None if it can't be fetched."""
        url = f"{self.files_url}/{artifact}/{version}/{artifact}-{version}.pom"
        try:
            return self._get(url).decode("utf-8")
        except urllib.error.HTTPError:
            return None

    def jar_java(self, artifact, version):
        """Required Java feature version from an artifact jar's bytecode.

        Reads the class-file major version of the artifact's own classes
        (ignoring multi-release ``META-INF/versions/`` overlays) and maps it to a
        Java feature version. This is the authoritative minimum — the JVM level
        the classes were compiled for. Returns None if the jar has no classes or
        cannot be read (e.g. an aggregator jar, or a 404).
        """
        url = f"{self.files_url}/{artifact}/{version}/{artifact}-{version}.jar"
        try:
            raw = self._get(url)
        except urllib.error.HTTPError:
            return None
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            return None
        max_major = 0
        for name in zf.namelist():
            if not name.endswith(".class") or name.startswith("META-INF/"):
                continue  # skip multi-release overlays
            data = zf.read(name)
            if len(data) >= 8 and data[:4] == CLASS_MAGIC:
                major = (data[6] << 8) | data[7]
                max_major = max(max_major, major)
        if not max_major:
            return None
        return JAVA_BY_CLASS_MAJOR.get(max_major, DEFAULT_JAVA)


# --- Auth loading -----------------------------------------------------------

def load_credentials():
    user = os.environ.get("GEMFIRE_ARTIFACTORY_USERNAME")
    pw = os.environ.get("GEMFIRE_ARTIFACTORY_PASSWORD")
    if user and pw:
        return user, pw
    # Fall back to ~/.gradle/gradle.properties for local runs.
    props = Path.home() / ".gradle" / "gradle.properties"
    if props.is_file():
        values = {}
        for line in props.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
        user = user or values.get("gemfireRepoUsername")
        pw = pw or values.get("gemfireRepoPassword")
    if not user or not pw:
        sys.exit(
            "ERROR: Artifactory credentials not found. Set "
            "GEMFIRE_ARTIFACTORY_USERNAME / GEMFIRE_ARTIFACTORY_PASSWORD or add "
            "gemfireRepoUsername / gemfireRepoPassword to ~/.gradle/gradle.properties."
        )
    return user, pw


# --- Family model -----------------------------------------------------------

class Family:
    """One (prefix, Spring Boot gen, GemFire version) artifact family."""

    def __init__(self, prefix, sb_gen, gf_ver):
        self.prefix = prefix
        self.sb_gen = sb_gen
        self.gf_ver = gf_ver
        self.artifacts = []          # bare artifact names (no group)
        self.versions = []           # all published versions
        self.release = None          # latest published version (concrete)
        self.java_version = None     # read from bytecode; None until resolved
        self.boot_gen = None         # real Spring Boot generation; None until resolved

    @property
    def slug(self):
        sb = self.sb_gen.replace(".", "-")
        gf = self.gf_ver.replace(".", "-")
        return f"{self.prefix}-for-vmware-gemfire-{sb}-{gf}"

    @property
    def filename(self):
        return f"{self.slug}.json"

    @property
    def coordinates(self):
        return [f"{GROUP_ID}:{name}" for name in sorted(self.artifacts)]

    @property
    def block_keys(self):
        """Distinct major.minor.x lines this family has published, sorted."""
        keys = sorted({block_key(v) for v in self.versions}, key=parse_version)
        return keys

    @property
    def top_block(self):
        return self.block_keys[-1] if self.block_keys else block_key(self.release)

    @property
    def java(self):
        if self.java_version:
            return self.java_version
        gen = self.boot_gen or self.sb_gen
        return FALLBACK_JAVA_BY_SB_MAJOR.get(sb_tuple(gen)[0], DEFAULT_JAVA)


def detect_families(artifacts, prefixes, min_gf):
    """Group Artifactory artifact names into families keyed (prefix, sb, gf)."""
    min_gf_t = parse_version(min_gf)
    families = {}
    for name in artifacts:
        prefix = _match_prefix(name, prefixes)
        if not prefix:
            continue
        nums = re.findall(r"\d+\.\d+", name)
        if len(nums) < 2:
            continue
        sb_gen, gf_ver = nums[0], nums[-1]
        if parse_version(gf_ver) < min_gf_t:
            continue
        key = (prefix, sb_gen, gf_ver)
        fam = families.get(key)
        if fam is None:
            fam = families[key] = Family(prefix, sb_gen, gf_ver)
        fam.artifacts.append(name)
    return families


def _match_prefix(name, prefixes):
    """Return the owning prefix for an artifact name, or None.

    Longest prefix wins so that 'spring-boot-session-*' is bucketed under
    'spring-boot' (it starts with 'spring-boot-'), while 'spring-session-*'
    is bucketed under 'spring-session'.
    """
    best = None
    for prefix in prefixes:
        if name.startswith(prefix + "-"):
            if best is None or len(prefix) > len(best):
                best = prefix
    return best


def _java_sample_priority(name):
    """Order coordinates so class-bearing artifacts are sampled first.

    Base / starter / actuator-prefix aggregator jars are often empty, so prefer
    autoconfigure, then core, then extensions, then everything else.
    """
    for i, kw in enumerate(("-autoconfigure-", "-core-", "-extensions-")):
        if kw in name:
            return (0, i, name)
    return (1, 0, name)


def resolve_family_java(artifactory, family, verbose=False):
    """Read the family's required Java version from its artifacts' bytecode.

    Samples coordinates (class-bearing ones first) at the family's release
    version until one yields a class-file major version. Falls back to the
    per-generation table if none can be read.
    """
    for name in sorted(family.artifacts, key=_java_sample_priority):
        java = artifactory.jar_java(name, family.release)
        if java:
            if verbose:
                print(f"    java={java} (bytecode of {name}:{family.release})")
            return java
    gen = family.boot_gen or family.sb_gen
    fallback = FALLBACK_JAVA_BY_SB_MAJOR.get(sb_tuple(gen)[0], DEFAULT_JAVA)
    if verbose:
        print(f"    java={fallback} (fallback table; no readable bytecode)")
    return fallback


# Dependency coordinate that reveals the real Spring Boot generation for a
# family whose own artifactId doesn't encode it (see BOOT_GEN_FROM_NAME_PREFIXES).
_BOOT_GEN_DEP_RE = re.compile(
    r"<artifactId>spring-(?:boot|data|session)-(\d+\.\d+)-gemfire-[\d.]+</artifactId>"
)


def resolve_family_boot_gen(artifactory, family, verbose=False):
    """Resolve the real Spring Boot generation this family requires.

    For spring-boot/spring-data/spring-session, the artifactId's own
    generation token already *is* the Spring Boot generation. spring-integration
    versions against the separate Spring Integration project and encodes *that*
    project's version instead (e.g. "6.5", "7.1") -- the actual Spring Boot
    generation isn't recoverable from the name, so it's read from the family's
    POM, which declares a compile dependency on the matching
    spring-data-{SB}-gemfire-{GF} coordinate.
    """
    if family.prefix in BOOT_GEN_FROM_NAME_PREFIXES:
        return family.sb_gen
    for name in sorted(family.artifacts, key=_java_sample_priority):
        pom = artifactory.pom(name, family.release)
        if not pom:
            continue
        m = _BOOT_GEN_DEP_RE.search(pom)
        if m:
            if verbose:
                print(f"    boot-gen={m.group(1)} (pom dependency of {name}:{family.release})")
            return m.group(1)
    if verbose:
        print(f"    boot-gen={family.sb_gen} (fallback; no spring-boot/data/session "
              f"dependency found in POM)")
    return family.sb_gen


# --- Mapping construction ---------------------------------------------------

def make_requirements(sb_gen, java):
    return {
        "supportedJavaVersions": {"major": java, "minor": java},
        "supportedGenerations": {"spring-boot": f"{sb_gen}.x"},
        "excludedArtifacts": [],
    }


def swap_sb(artifact_name, target_sb):
    """Swap the Spring Boot generation token (first d.d) in an artifact name."""
    return re.sub(r"\d+\.\d+", target_sb, artifact_name, count=1)


def swap_gf(artifact_name, target_gf):
    """Swap the GemFire-generation token (last d.d) in an artifact name."""
    matches = list(re.finditer(r"\d+\.\d+", artifact_name))
    if not matches:
        return artifact_name
    last = matches[-1]
    return artifact_name[:last.start()] + target_gf + artifact_name[last.end():]


def build_recipes(source, target):
    """One ChangeDependency per source coord whose SB-swapped name exists in target."""
    target_names = set(target.artifacts)
    recipes = []
    for old_name in sorted(source.artifacts):
        new_name = swap_sb(old_name, target.sb_gen)
        if new_name not in target_names:
            continue  # target family has no counterpart; drop (also kills dead coords)
        recipes.append({
            "name": CHANGE_DEPENDENCY_RECIPE,
            "params": {
                "oldGroupId": GROUP_ID,
                "oldArtifactId": old_name,
                "newGroupId": GROUP_ID,
                "newArtifactId": new_name,
                "newVersion": target.release,
            },
        })
    return recipes


def build_gf_recipes(source, target):
    """One ChangeDependency per source coord whose GF-swapped name exists in target.

    Mirrors build_recipes(), but swaps the GemFire-generation token instead of
    the Spring Boot token. Used by wire_gf_realignment() for the reactive
    cross-GemFire-line hop on the generic mapping.
    """
    target_names = set(target.artifacts)
    recipes = []
    for old_name in sorted(source.artifacts):
        new_name = swap_gf(old_name, target.gf_ver)
        if new_name not in target_names:
            continue  # this artifact variant has no counterpart on the target line
        recipes.append({
            "name": CHANGE_DEPENDENCY_RECIPE,
            "params": {
                "oldGroupId": GROUP_ID,
                "oldArtifactId": old_name,
                "newGroupId": GROUP_ID,
                "newArtifactId": new_name,
                "newVersion": target.release,
            },
        })
    return recipes


def build_mapping(family):
    """Build the mapping dict for a family with intra-family links only.

    Cross-family hop recipes / nextRewrite and any concrete alias blocks are
    layered on afterwards by wire_chain().
    """
    keys = family.block_keys
    rewrite = {}
    for i, key in enumerate(keys):
        nxt = keys[i + 1] if i + 1 < len(keys) else None
        rewrite[key] = {
            "recipes": [],
            "nextRewrite": nxt,  # string within-family bump, or None for top block
            "requirements": make_requirements(family.boot_gen, family.java),
        }
    return {
        "slug": family.slug,
        "coordinates": family.coordinates,
        "repositoryUrl": f"https://github.com/gemfire/{family.prefix}-for-vmware-gemfire",
        "rewrite": rewrite,
    }


def wire_chain(prefix, gf_ver, families, mappings, verbose):
    """Wire within-GF-line hops for one (prefix, gf) chain across SB gens."""
    line = sorted(
        (f for (p, _sb, g), f in families.items() if p == prefix and g == gf_ver),
        key=lambda f: sb_tuple(f.sb_gen),
    )
    if verbose:
        chain = " -> ".join(f.sb_gen for f in line)
        print(f"  {prefix} / gemfire-{gf_ver}: {chain}")

    # First pass: wire every top-block hop (recipes + nextRewrite). Alias blocks
    # are deferred to a second pass so they clone a target top block that has
    # already been wired to continue (or terminate) the chain.
    pending_aliases = []
    for i, source in enumerate(line):
        src_map = mappings[source.slug]
        src_key = source.top_block
        if i + 1 >= len(line):
            # Terminal family: top block stays terminal (no recipes, null next).
            src_map["rewrite"][src_key]["nextRewrite"] = None
            continue

        target = line[i + 1]
        tgt_map = mappings[target.slug]
        tgt_key = target.top_block
        recipes = build_recipes(source, target)

        if src_key != tgt_key:
            # Land directly on the target's real top block (which continues the
            # chain). Distinct strings already satisfy the version-delta rule.
            land_version = tgt_key
        else:
            # Same block-key string on both sides would make App Advisor drop
            # the recipes. Land on a concrete version and add an alias block.
            land_version = target.release
            pending_aliases.append((tgt_map, target, land_version))

        src_map["rewrite"][src_key]["recipes"] = recipes
        src_map["rewrite"][src_key]["nextRewrite"] = {
            "version": land_version,
            "project": target.slug,
        }

    # Second pass: materialize alias blocks now that top blocks are fully wired.
    for tgt_map, target, land_version in pending_aliases:
        _ensure_alias_block(tgt_map, target, land_version)


def _ensure_alias_block(target_map, target, version):
    """Add a concrete alias block to the target, cloning its top block behavior.

    The alias mirrors the target's top block so that landing on the concrete
    version continues (or terminates) the chain exactly as the top block does.
    """
    if version in target_map["rewrite"]:
        return
    top = target_map["rewrite"][target.top_block]
    target_map["rewrite"][version] = json.loads(json.dumps(top))  # deep copy


def wire_gf_realignment(families, rewrite, latest_by_key, verbose):
    """Wire the reactive GemFire-line realignment hop onto the generic mapping.

    Deliberately separate from wire_chain()'s Spring-Boot-driven, within-line
    hops: this reacts to the *native* gemfire-core coordinate's own version
    (e.g. a manual GEMFIRE_VERSION bump), not to a Spring Boot upgrade. Once a
    project's raw GemFire artifacts have already landed on a newer line (say
    10.2.x) while its spring-boot/spring-data/spring-session bridge
    coordinates are still named for an older line (-10.1), this rewrites the
    bridge coordinates to match -- for whichever Spring Boot generation both
    lines publish. It never initiates the native-artifact jump; it only
    reacts once the project has already made it deliberately.
    """
    gf_vers = sorted({gf for (_p, _sb, gf) in families}, key=parse_version)
    by_gf = defaultdict(list)
    for (_prefix, _sb_gen, gf_ver), fam in families.items():
        by_gf[gf_ver].append(fam)

    for idx, gf_target in enumerate(gf_vers):
        key = f"{gf_target}.x"
        if key not in rewrite:
            continue  # gemfire-core itself never published this line
        recipes = []
        for gf_source in gf_vers[:idx]:
            for source in by_gf[gf_source]:
                target = next(
                    (f for f in by_gf[gf_target]
                     if f.prefix == source.prefix and f.sb_gen == source.sb_gen),
                    None,
                )
                if target is None:
                    continue  # this Spring Boot generation isn't published on gf_target
                recipes.extend(build_gf_recipes(source, target))
        if not recipes:
            continue

        land_version = latest_by_key[key]
        if verbose:
            targets = sorted({r["params"]["newArtifactId"] for r in recipes})
            print(f"  {GENERIC_MAPPING_FILE}: {key} realigns -> {', '.join(targets)}")

        alias = json.loads(json.dumps(rewrite[key]))  # pristine terminal clone, pre-hop
        rewrite[key]["recipes"] = recipes
        # Plain-string form (stay within this same mapping) -- deliberately NOT the
        # object form with "project": generic_slug, which would self-reference this
        # very mapping. No other hop in this corpus points a mapping at itself; avoid
        # being the first in case a graph walker (e.g. under -f/--force, which walks
        # the full transitive plan) doesn't guard the trivial self-loop case.
        rewrite[key]["nextRewrite"] = land_version
        if land_version not in rewrite:
            rewrite[land_version] = alias


def build_generic_mapping(artifactory, mappings_dir, families, verbose):
    """Refresh gemfire-10-x.json version blocks from published gemfire-core lines."""
    path = mappings_dir / GENERIC_MAPPING_FILE
    if not path.is_file():
        if verbose:
            print(f"  {GENERIC_MAPPING_FILE} not present; skipping generic mapping")
        return None
    existing = json.loads(path.read_text())

    versions, _ = artifactory.versions("gemfire-core")
    by_line = defaultdict(list)
    for v in versions:
        by_line[block_key(v)].append(v)
    keys = sorted(by_line, key=parse_version)
    if verbose:
        print(f"  {GENERIC_MAPPING_FILE}: core lines {', '.join(keys)}")

    # Preserve the curated coordinate list; only version blocks are managed.
    # Read the required Java for each line from that line's latest gemfire-core.
    rewrite = {}
    latest_by_key = {}
    for key in keys:
        latest = max(by_line[key], key=parse_version)
        latest_by_key[key] = latest
        java = artifactory.jar_java("gemfire-core", latest) or GENERIC_MAPPING_JAVA
        if verbose:
            print(f"    {key}: java={java} (gemfire-core:{latest})")
        rewrite[key] = {
            "recipes": [],
            "nextRewrite": None,
            "requirements": {
                "supportedJavaVersions": {"major": java, "minor": java},
                "supportedGenerations": {},
                "excludedArtifacts": [],
            },
        }
    wire_gf_realignment(families, rewrite, latest_by_key, verbose)
    existing["rewrite"] = rewrite
    return existing


# --- Output -----------------------------------------------------------------

def write_mapping(path, data, dry_run):
    text = json.dumps(data, indent=2) + "\n"
    if path.is_file() and path.read_text() == text:
        return "unchanged"
    action = "would write" if dry_run else ("update" if path.is_file() else "create")
    if not dry_run:
        path.write_text(text)
    return action


def main():
    parser = argparse.ArgumentParser(description="Generate GemFire mapping files from Artifactory.")
    repo_root = Path(__file__).resolve().parent.parent
    parser.add_argument("--mappings-dir", default=str(repo_root / ".advisor" / "mappings"))
    parser.add_argument("--prefixes", default=",".join(PREFIXES),
                        help="Comma-separated subset of: " + ",".join(PREFIXES))
    parser.add_argument("--min-gf", default=DEFAULT_MIN_GF)
    parser.add_argument("--artifactory-root",
                        help="Artifactory root URL up to and including /artifactory "
                             "(env GEMFIRE_ARTIFACTORY_ROOT; "
                             f"default {DEFAULT_ARTIFACTORY_ROOT})")
    parser.add_argument("--artifactory-repo",
                        help="Artifactory repository key holding the GemFire artifacts "
                             "(env GEMFIRE_ARTIFACTORY_REPO; "
                             f"default {DEFAULT_ARTIFACTORY_REPO})")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--delete-orphans", action="store_true",
                        help="Delete mapping files with no matching Artifactory family "
                             "(combined with --dry-run: prints what would be deleted)")
    args = parser.parse_args()

    mappings_dir = Path(args.mappings_dir)
    if not mappings_dir.is_dir():
        sys.exit(f"ERROR: mappings dir not found: {mappings_dir}")
    prefixes = tuple(p.strip() for p in args.prefixes.split(",") if p.strip())

    root = (args.artifactory_root or os.environ.get("GEMFIRE_ARTIFACTORY_ROOT")
            or DEFAULT_ARTIFACTORY_ROOT)
    repo = (args.artifactory_repo or os.environ.get("GEMFIRE_ARTIFACTORY_REPO")
            or DEFAULT_ARTIFACTORY_REPO)
    files_url, storage_url = resolve_endpoints(root, repo)

    user, pw = load_credentials()
    artifactory = Artifactory(user, pw, files_url, storage_url)

    print(f"Fetching Artifactory catalog from {files_url} ...")
    artifacts = artifactory.list_artifacts()
    families = detect_families(artifacts, prefixes, args.min_gf)
    print(f"Detected {len(families)} families (prefixes={','.join(prefixes)}, min-gf={args.min_gf}).")

    # Fetch versions for each family's base artifact.
    for fam in families.values():
        base = f"{fam.prefix}-{fam.sb_gen}-gemfire-{fam.gf_ver}"
        versions, release = artifactory.versions(base)
        if not versions:
            # Fall back to any coordinate in the family.
            for name in sorted(fam.artifacts):
                versions, release = artifactory.versions(name)
                if versions:
                    break
        fam.versions = versions
        fam.release = release
        if args.verbose:
            print(f"  {fam.slug}: versions={versions} release={release} "
                  f"coords={len(fam.artifacts)}")
        if not versions:
            print(f"  WARNING: no versions found for {fam.slug}; skipping")
            continue
        fam.boot_gen = resolve_family_boot_gen(artifactory, fam, args.verbose)
        fam.java_version = resolve_family_java(artifactory, fam, args.verbose)

    families = {k: f for k, f in families.items() if f.versions}

    # Build base mappings (intra-family links) then wire cross-family hops.
    mappings = {fam.slug: build_mapping(fam) for fam in families.values()}
    chains = sorted({(p, g) for (p, _sb, g) in families})
    print("Wiring hop chains (within-GF-line)...")
    for prefix, gf_ver in chains:
        wire_chain(prefix, gf_ver, families, mappings, args.verbose)

    # Emit family mappings.
    results = {}
    for slug, data in sorted(mappings.items()):
        path = mappings_dir / f"{slug}.json"
        results[slug] = write_mapping(path, data, args.dry_run)

    # Generic core-gemfire mapping.
    generic = build_generic_mapping(artifactory, mappings_dir, families, args.verbose)
    if generic is not None:
        results[GENERIC_MAPPING_FILE] = write_mapping(
            mappings_dir / GENERIC_MAPPING_FILE, generic, args.dry_run)

    # Orphaned mapping files: *-for-vmware-gemfire-*.json present on disk whose
    # family is no longer (or not yet) published on Artifactory.
    managed = {f"{slug}.json" for slug in mappings}
    orphan_paths = sorted(
        p for p in mappings_dir.glob("*-for-vmware-gemfire-*.json")
        if p.name not in managed
    )
    if orphan_paths:
        if args.delete_orphans:
            verb = "would delete" if args.dry_run else "deleting"
            print(f"\n{len(orphan_paths)} orphan gemfire mapping file(s) ({verb}):")
            for p in orphan_paths:
                print(f"  {p.name}")
                if not args.dry_run:
                    p.unlink()
        else:
            print(f"\nWARNING: {len(orphan_paths)} gemfire mapping file(s) with no matching "
                  f"Artifactory family (left untouched; use --delete-orphans to remove):")
            for p in orphan_paths:
                print(f"  {p.name}")

    # Report.
    changed = {s: a for s, a in results.items() if a != "unchanged"}
    print(f"\n{len(results)} mapping files processed, {len(changed)} changed:")
    for slug in sorted(changed):
        print(f"  [{changed[slug]}] {slug}")
    if not changed:
        print("  (all up to date)")


if __name__ == "__main__":
    main()
