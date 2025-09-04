#!/usr/bin/env python3

import xml.etree.ElementTree as ET
import json
import glob
import os
import re
import sys
import argparse

# Dependency mappings
# Use None for "any artifact from this group"
# Use exact string for exact match
# Use regex pattern (with ^ and $) for pattern matching
dep_mappings = {
    "spring-boot": ("org.springframework.boot", None),
    "spring-data-commons": ("org.springframework.data", "spring-data-commons"),
    "spring-data-jpa": ("org.springframework.data", "spring-data-jpa"),
    "spring-data-mongodb": ("org.springframework.data", "spring-data-mongodb"),
    "spring-data-redis": ("org.springframework.data", "spring-data-redis"),
    "spring-data-relational": ("org.springframework.data", "spring-data-relational"),
    "spring-framework": ("org.springframework", r"^spring-core$|^spring-context$|^spring-beans$|^spring-web$"),
    "spring-security": ("org.springframework.security", r"^spring-security-.*"),
    "spring-integration": ("org.springframework.integration", r"^spring-integration-.*"),
    "spring-kafka": ("org.springframework.kafka", "spring-kafka"),
    "spring-retry": ("org.springframework.retry", "spring-retry"),
    "spring-cloud-azure": ("com.azure.spring", None),  # Any artifact from com.azure.spring
    "spring-cloud-commons": ("org.springframework.cloud", "spring-cloud-commons"),
    "spring-cloud-function": ("org.springframework.cloud", r"^spring-cloud-function-.*"),
    "spring-cloud-stream": ("org.springframework.cloud", "spring-cloud-stream"),
    "spring-cloud-bus": ("org.springframework.cloud", "spring-cloud-bus"),
    "spring-cloud-sleuth": ("org.springframework.cloud", "spring-cloud-sleuth"),
    "azure-sdk-for-java": ("com.azure", "azure-core"),  # Renamed from azure-core
    "micrometer": ("io.micrometer", "micrometer-core"),  # Exact match
    "reactor": ("io.projectreactor", "reactor-core"),  # Exact match
    "reactor-netty": ("io.projectreactor.netty", r"^reactor-netty.*"),
    "jedis": ("redis.clients", "jedis")  # Exact match
}

def normalize_java_version(version_str):
    """Normalize Java version string to an integer for comparison.
    Handles formats like '1.8', '8', '11', '17', etc."""
    if not version_str:
        return 0

    version_str = version_str.strip()

    # Handle "1.x" format (e.g., "1.8" -> 8)
    if version_str.startswith("1."):
        try:
            return int(version_str.split(".")[1])
        except (IndexError, ValueError):
            return 0

    # Handle direct version numbers
    try:
        # Take only the major version number
        return int(re.match(r'^\d+', version_str).group())
    except (AttributeError, ValueError):
        return 0

def version_to_x(version_str):
    """Convert version like 1.2.3 to 1.2.x"""
    parts = version_str.split('.')
    if len(parts) >= 3:
        return f"{parts[0]}.{parts[1]}.x"
    return version_str

def compare_versions(v1, v2):
    """Compare two version strings, return the larger one"""
    try:
        # Remove any -SNAPSHOT, -RELEASE, etc suffixes for comparison
        v1_clean = re.split(r'[-_]', v1)[0]
        v2_clean = re.split(r'[-_]', v2)[0]

        # Split versions into parts and compare
        v1_parts = [int(x) for x in v1_clean.split('.') if x.isdigit()]
        v2_parts = [int(x) for x in v2_clean.split('.') if x.isdigit()]

        # Pad shorter version with zeros
        max_len = max(len(v1_parts), len(v2_parts))
        v1_parts.extend([0] * (max_len - len(v1_parts)))
        v2_parts.extend([0] * (max_len - len(v2_parts)))

        # Compare each part
        for p1, p2 in zip(v1_parts, v2_parts):
            if p1 > p2:
                return v1
            elif p1 < p2:
                return v2

        # If equal, return the first one
        return v1
    except:
        # Fallback to string comparison if parsing fails
        return max(v1, v2)

def extract_xml_from_maven_output(file_path):
    """Extract all POM XML sections from Maven output"""
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    xml_sections = []

    # For effective-pom output with multiple modules, each module's effective POM
    # is printed separately. We need to extract all of them.

    # Pattern 1: Look for standalone project sections
    project_pattern = r'(<project[^>]*>.*?</project>)'
    matches = re.findall(project_pattern, content, re.DOTALL)

    if matches:
        xml_sections.extend(matches)
    else:
        # Pattern 2: Try to find XML declarations followed by project
        xml_pattern = r'(<\?xml[^>]*\?>.*?</project>)'
        matches = re.findall(xml_pattern, content, re.DOTALL)
        if matches:
            xml_sections.extend(matches)

    # If still no matches, try to extract the main content
    if not xml_sections:
        xml_start = content.find('<?xml')
        if xml_start == -1:
            xml_start = content.find('<project')

        if xml_start != -1:
            xml_end = content.rfind('</project>')
            if xml_end != -1:
                xml_sections.append(content[xml_start:xml_end + len('</project>')])

    return xml_sections

def infer_java_version_from_deps(deps):
    """Infer Java version based on dependency versions"""
    if not deps:
        return None

    # Check Spring Boot version first (most reliable indicator)
    if 'spring-boot' in deps:
        boot_version = deps['spring-boot']
        # Spring Boot 3.x requires Java 17+
        if boot_version.startswith('3.'):
            return "17"
        # Spring Boot 2.7.x supports Java 8-17, but commonly uses 11
        elif boot_version.startswith('2.7'):
            return "11"
        # Spring Boot 2.6.x and earlier support Java 8
        elif boot_version.startswith('2.'):
            return "8"

    # Check Spring Framework version as fallback
    if 'spring-framework' in deps:
        framework_version = deps['spring-framework']
        # Spring Framework 6.x requires Java 17+
        if framework_version.startswith('6.'):
            return "17"
        # Spring Framework 5.3.x supports Java 8-17
        elif framework_version.startswith('5.3'):
            return "8"
        # Spring Framework 5.x supports Java 8
        elif framework_version.startswith('5.'):
            return "8"

    # Check Reactor version
    if 'reactor' in deps:
        reactor_version = deps['reactor']
        # Reactor 3.5+ typically used with Java 17
        if reactor_version.startswith('3.5') or reactor_version.startswith('3.6'):
            return "17"
        # Earlier Reactor 3.x versions support Java 8
        elif reactor_version.startswith('3.'):
            return "8"

    return None

def extract_java_version_from_plugin(root, ns, prefix):
    """Extract Java version from maven-compiler-plugin configuration"""
    # Look in build/plugins/plugin for maven-compiler-plugin
    build = root.find(f'{prefix}build', ns) if prefix else root.find('build')
    if build is not None:
        plugins = build.find(f'{prefix}plugins', ns) if prefix else build.find('plugins')
        if plugins is not None:
            plugin_elements = plugins.findall(f'{prefix}plugin', ns) if prefix else plugins.findall('plugin')
            for plugin in plugin_elements:
                artifact_id = plugin.find(f'{prefix}artifactId', ns) if prefix else plugin.find('artifactId')
                if artifact_id is not None and artifact_id.text == 'maven-compiler-plugin':
                    config = plugin.find(f'{prefix}configuration', ns) if prefix else plugin.find('configuration')
                    if config is not None:
                        # Check for source, target, or release
                        for tag in ['release', 'target', 'source']:
                            elem = config.find(f'{prefix}{tag}', ns) if prefix else config.find(tag)
                            if elem is not None and elem.text:
                                return elem.text.strip()
    return None

def extract_deps_from_xml(xml_content, module_num=0, verbose=False, fallback_java_version=None):
    """Extract dependencies from a single XML project section"""
    deps = {}
    java_version = None

    try:
        root = ET.fromstring(xml_content)

        # Handle namespace
        ns = {}
        if root.tag.startswith('{'):
            # Extract namespace from root tag
            ns_match = re.match(r'\{(.*?)\}', root.tag)
            if ns_match:
                ns = {'m': ns_match.group(1)}

        # Try without namespace first, then with namespace
        for use_ns in [False, True]:
            if java_version is not None:
                break

            if use_ns and ns:
                prefix = 'm:'
                ns_dict = ns
            else:
                prefix = ''
                ns_dict = {}

            # Strategy 1: Look for Java version in properties
            properties = root.find(f'{prefix}properties', ns_dict)
            if properties is not None:
                # Check various Java version properties
                java_props = ['java.version', 'maven.compiler.target', 'maven.compiler.source',
                             'maven.compiler.release', 'project.build.sourceLevel']
                for prop in java_props:
                    elem = properties.find(f'{prefix}{prop}', ns_dict)
                    if elem is None and not use_ns:
                        # Try without prefix even in properties
                        elem = properties.find(prop)
                    if elem is not None and elem.text:
                        java_version = elem.text.strip()
                        if verbose:
                            print(f"    Module {module_num}: Found Java version in properties/{prop}: {java_version}")
                        break

            # Strategy 2: Try maven-compiler-plugin configuration
            if java_version is None:
                java_version = extract_java_version_from_plugin(root, ns_dict, prefix)
                if java_version and verbose:
                    print(f"    Module {module_num}: Found Java version in maven-compiler-plugin: {java_version}")

        # Extract dependencies
        for use_ns in [False, True]:
            if use_ns and ns:
                prefix = 'm:'
                ns_dict = ns
            else:
                prefix = ''
                ns_dict = {}

            dependencies = root.findall(f'.//{prefix}dependency', ns_dict)
            if not dependencies and not use_ns:
                # Try finding dependencies without namespace
                dependencies = root.findall('.//dependency')

            for dep in dependencies:
                group_elem = dep.find(f'{prefix}groupId', ns_dict) if use_ns else dep.find('groupId')
                artifact_elem = dep.find(f'{prefix}artifactId', ns_dict) if use_ns else dep.find('artifactId')
                version_elem = dep.find(f'{prefix}version', ns_dict) if use_ns else dep.find('version')
                scope_elem = dep.find(f'{prefix}scope', ns_dict) if use_ns else dep.find('scope')

                if group_elem is None or version_elem is None:
                    continue

                # Check scope (compile is default)
                scope = scope_elem.text if scope_elem is not None else 'compile'
                if scope != 'compile':
                    continue

                group_id = group_elem.text.strip() if group_elem.text else ""
                artifact_id = artifact_elem.text.strip() if artifact_elem is not None and artifact_elem.text else ""
                version_str = version_elem.text.strip() if version_elem.text else ""

                # Check against our mappings
                for dep_name, (target_group, artifact_pattern) in dep_mappings.items():
                    if group_id == target_group:
                        matched = False

                        if artifact_pattern is None:
                            # Match any artifact from this group
                            matched = True
                        elif artifact_pattern.startswith('^'):
                            # It's a regex pattern
                            if re.match(artifact_pattern, artifact_id):
                                matched = True
                        else:
                            # Exact match required
                            if artifact_id == artifact_pattern:
                                matched = True

                        if matched:
                            if verbose:
                                print(f"    Module {module_num}: Matched {dep_name}: {group_id}:{artifact_id}:{version_str}")
                            deps[dep_name] = version_str
                            break

        # Strategy 3: Infer from dependencies if Java version still not found
        if java_version is None and deps:
            inferred_version = infer_java_version_from_deps(deps)
            if inferred_version:
                java_version = inferred_version
                if verbose:
                    print(f"    Module {module_num}: Inferred Java version from dependencies: {java_version}")

        # Strategy 4: Use fallback if provided
        if java_version is None and fallback_java_version:
            java_version = fallback_java_version
            if verbose:
                print(f"    Module {module_num}: Using fallback Java version: {java_version}")

    except ET.ParseError as e:
        if verbose:
            print(f"    Module {module_num}: XML Parse error: {e}")
    except Exception as e:
        if verbose:
            print(f"    Module {module_num}: Error: {e}")

    return java_version, deps

def extract_compile_deps_multi_module(pom_file, verbose=False, fallback_java_version=None):
    """Extract compile-scope dependencies from multi-module POM"""
    try:
        # Extract all XML sections from the Maven output
        xml_sections = extract_xml_from_maven_output(pom_file)

        if not xml_sections:
            print(f"Warning: Could not find valid XML in {pom_file}")
            return None, {}

        print(f"  Found {len(xml_sections)} module(s) in {pom_file}")

        # Collect all dependencies from all modules
        all_deps = {}
        java_versions = []

        for i, xml_content in enumerate(xml_sections):
            module_java_version, module_deps = extract_deps_from_xml(
                xml_content, i, verbose, fallback_java_version
            )

            # Collect all Java versions found
            if module_java_version:
                java_versions.append(module_java_version)
                if verbose:
                    print(f"    Module {i} Java version: {module_java_version}")

            # Merge dependencies, keeping the highest version for each
            for dep_name, dep_version in module_deps.items():
                if dep_name in all_deps:
                    # Compare versions and keep the higher one
                    old_version = all_deps[dep_name]
                    new_version = compare_versions(old_version, dep_version)
                    if verbose and old_version != new_version:
                        print(f"    Version conflict for {dep_name}: {old_version} vs {dep_version} -> using {new_version}")
                    all_deps[dep_name] = new_version
                else:
                    all_deps[dep_name] = dep_version

        # For Java version, pick the highest version found
        java_version = None
        if java_versions:
            # Find the highest Java version using normalized comparison
            highest_version = None
            highest_normalized = 0

            for v in java_versions:
                normalized = normalize_java_version(v)
                if normalized > highest_normalized:
                    highest_normalized = normalized
                    highest_version = v

            java_version = highest_version

            if verbose and len(set(java_versions)) > 1:
                print(f"    Multiple Java versions found: {set(java_versions)}, using highest: {java_version}")

        # If still no Java version, try to infer from collected dependencies
        if java_version is None and all_deps:
            inferred_version = infer_java_version_from_deps(all_deps)
            if inferred_version:
                java_version = inferred_version
                if verbose:
                    print(f"    Inferred Java version from aggregated dependencies: {java_version}")

        # Use fallback if still nothing
        if java_version is None and fallback_java_version:
            java_version = fallback_java_version
            if verbose:
                print(f"    Using fallback Java version: {java_version}")

        # Convert versions to x format
        final_deps = {}
        for dep_name, dep_version in all_deps.items():
            final_deps[dep_name] = version_to_x(dep_version)

        return java_version, final_deps

    except Exception as e:
        print(f"Error processing {pom_file}: {e}")
        import traceback
        if verbose:
            traceback.print_exc()
        return None, {}

def process_pom_directory(pom_dir, clean_xml=False, verbose=False, fallback_java_version=None):
    """Process all effective-pom-*.xml files in the given directory"""

    if not os.path.exists(pom_dir):
        print(f"Error: Directory '{pom_dir}' does not exist")
        return 1

    pom_files = glob.glob(os.path.join(pom_dir, "effective-pom-*.xml"))

    if not pom_files:
        print(f"Warning: No effective-pom-*.xml files found in '{pom_dir}'")
        return 1

    print(f"Processing {len(pom_files)} POM files in '{pom_dir}'...")

    # Process each POM file
    for pom_file in pom_files:
        tag = os.path.basename(pom_file).replace('effective-pom-', '').replace('.xml', '')
        version_with_x = version_to_x(tag)

        # Optionally save cleaned XML
        if clean_xml:
            xml_sections = extract_xml_from_maven_output(pom_file)
            if xml_sections:
                for i, xml_content in enumerate(xml_sections):
                    suffix = f"-module{i}" if i > 0 else ""
                    clean_file = pom_file.replace('.xml', f'{suffix}-clean.xml')
                    with open(clean_file, 'w') as f:
                        f.write(xml_content)
                    print(f"  Saved clean XML: {clean_file}")

        java_version, deps = extract_compile_deps_multi_module(pom_file, verbose, fallback_java_version)

        output = {
            "version": version_with_x,
            "javaVersion": java_version or "unknown",
            "deps": deps
        }

        # Write JSON file
        json_file = os.path.join(pom_dir, f"{tag}.json")
        with open(json_file, 'w') as f:
            json.dump(output, f, indent=2)

        print(f"Generated: {json_file}")
        if deps:
            print(f"  Found dependencies: {', '.join(deps.keys())}")
        if verbose:
            print(f"  Java version: {java_version or 'unknown'}")

    return 0

def main():
    parser = argparse.ArgumentParser(
        description='Extract dependency information from Maven effective POMs and generate JSON files'
    )
    parser.add_argument(
        'pom_dir',
        help='Directory containing effective-pom-*.xml files to process'
    )
    parser.add_argument(
        '--clean-xml', '-c',
        action='store_true',
        help='Also save cleaned XML files (without Maven output)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    parser.add_argument(
        '--default-java-version', '-j',
        help='Default Java version to use when not found in POM (e.g., 8, 11, 17)'
    )

    args = parser.parse_args()

    return process_pom_directory(args.pom_dir, args.clean_xml, args.verbose, args.default_java_version)

if __name__ == "__main__":
    sys.exit(main())