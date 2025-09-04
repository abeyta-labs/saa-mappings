#!/usr/bin/env python3
"""
Script to update Azure SDK for Java mappings by analyzing repository tags and generating
dependency information for missing versions.

Usage:
    python update_azure_sdk_mappings.py <mapping-name>

Example:
    python update_azure_sdk_mappings.py azure-core-http-netty
"""

import json
import os
import subprocess
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import re
from collections import defaultdict

def run_command(cmd: List[str], cwd: Optional[str] = None, capture_output: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=capture_output, text=True)
    if check and result.returncode != 0:
        print(f"Command failed with return code {result.returncode}")
        print(f"Error output: {result.stderr}")
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    return result

def read_json_file(filepath: str) -> dict:
    """Read and parse a JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)

def write_json_file(filepath: str, data: dict):
    """Write data to a JSON file with proper formatting."""
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, separators=(',', ' : '))

def get_existing_versions(mapping_data: dict) -> List[str]:
    """Extract existing versions from the mapping file."""
    versions = []
    if 'rewrite' in mapping_data:
        versions = list(mapping_data['rewrite'].keys())
    return versions

def parse_version(version_str: str) -> Tuple[int, int, int]:
    """Parse a version string like '1.16.0' or 'azure-core-http-netty_1.16.0' into (major, minor, patch)."""
    # Extract version number from tag format
    version_match = re.search(r'(\d+)\.(\d+)\.(\d+)', version_str)
    if version_match:
        return tuple(map(int, version_match.groups()))
    return (0, 0, 0)

def get_major_minor_string(major: int, minor: int) -> str:
    """Convert major.minor to the format used in mapping file (e.g., '1.16.x')."""
    return f"{major}.{minor}.x"

def get_repo_tags(repo_path: str, tag_prefix: str) -> List[str]:
    """Get all tags from the repository with the given prefix."""
    result = run_command(['git', 'tag', '-l', f'{tag_prefix}*'], cwd=repo_path)
    tags = result.stdout.strip().split('\n')
    return [tag for tag in tags if tag]  # Filter out empty strings

def group_tags_by_major_minor(tags: List[str]) -> Dict[Tuple[int, int], List[str]]:
    """Group tags by major.minor version, keeping only the latest patch for each."""
    grouped = defaultdict(list)

    for tag in tags:
        major, minor, patch = parse_version(tag)
        if major > 0:  # Valid version
            grouped[(major, minor)].append((patch, tag))

    # Keep only the latest patch version for each major.minor
    latest_tags = {}
    for (major, minor), patches in grouped.items():
        patches.sort(key=lambda x: x[0], reverse=True)
        latest_tags[(major, minor)] = patches[0][1]  # Get the tag with highest patch

    return latest_tags

def checkout_tag(repo_path: str, tag: str):
    """Checkout a specific tag in the repository."""
    run_command(['git', 'checkout', tag], cwd=repo_path)

def generate_effective_pom(repo_path: str, version: str, output_dir: str, module_path: Optional[str] = None):
    output_file = os.path.join(output_dir, f"effective-pom-{version}.xml")

    if module_path:
        # NEW: Calculate path from module directory
        module_full_path = os.path.join(repo_path, module_path)
        rel_path = os.path.relpath(output_file, module_full_path)
    else:
        # Original: Calculate path from repo root
        rel_path = os.path.relpath(output_file, repo_path)

    cmd = ['mvn', 'help:effective-pom', '-q', f'-Doutput={rel_path}']
    if module_path:
        cmd.extend(['-pl', module_path])

    run_command(cmd, cwd=repo_path)
    return output_file

def extract_pom_dependencies(script_path: str, effective_pom_path: str):
    """Run the extract_pom_deps.py script on the effective POM."""
    # The script expects the directory path, not the file path
    pom_dir = os.path.dirname(effective_pom_path)
    run_command(['python3', script_path, pom_dir])

def create_rewrite_object(version_data: dict) -> dict:
    """Create a rewrite object from the version.json data."""
    java_version = int(version_data.get("javaVersion", 11))
    supported_generations = version_data.get("deps", {})

    # Build supportedJavaVersions
    supported_java_versions = {
        "minor": java_version
    }

    # Add major version if spring-boot is present in supportedGenerations
    if "spring-boot" in supported_generations:
        supported_java_versions["major"] = java_version

    rewrite = {
        "recipes": [],
        "nextRewrite": {
            "version": None,  # Will be set later based on order
            "project": None
        },
        "requirements": {
            "supportedJavaVersions": supported_java_versions,
            "supportedGenerations": supported_generations,
            "excludedArtifacts": []
        }
    }
    return rewrite

def ensure_java_version_consistency(rewrite_dict: dict) -> int:
    """
    Ensure major Java version is set when spring-boot is present.
    Returns the number of fixes made.
    """
    fixes_made = 0
    for version, config in rewrite_dict.items():
        if 'requirements' not in config:
            continue

        requirements = config['requirements']

        # Check if spring-boot is in supportedGenerations
        if 'supportedGenerations' in requirements and 'spring-boot' in requirements.get('supportedGenerations', {}):
            # Ensure major version matches minor version
            if 'supportedJavaVersions' in requirements:
                java_versions = requirements['supportedJavaVersions']
                minor_version = java_versions.get('minor')
                major_version = java_versions.get('major')

                if minor_version and major_version != minor_version:
                    java_versions['major'] = minor_version
                    fixes_made += 1
                    print(f"  Fixed {version}: Set major={minor_version} for Spring Boot consistency")

    return fixes_made

def sort_versions(version_list: List[str]) -> List[str]:
    """Sort version strings like '1.5.x' in proper numerical order."""
    def version_key(v: str):
        parts = v.replace('.x', '').split('.')
        # Pad with zeros for proper sorting
        major = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
        minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return (major, minor)

    return sorted(version_list, key=version_key)

def sort_rewrite_dict(rewrite_dict: dict) -> dict:
    """Sort the rewrite dictionary by version and return a new ordered dict."""
    sorted_versions = sort_versions(list(rewrite_dict.keys()))
    sorted_dict = {}
    for version in sorted_versions:
        sorted_dict[version] = rewrite_dict[version]
    return sorted_dict

def update_next_rewrite_links(rewrite_dict: dict):
    """Update the nextRewrite links to maintain the chain."""
    versions = sort_versions(list(rewrite_dict.keys()))

    for i, version in enumerate(versions):
        if i < len(versions) - 1:
            # Not the last version - point to next version
            rewrite_dict[version]["nextRewrite"] = {
                "version": versions[i + 1],
                "project": None
            }
        else:
            # Last version - set nextRewrite to null
            rewrite_dict[version]["nextRewrite"] = None

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Update Azure SDK for Java mappings by analyzing repository tags.',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        'mapping_name',
        type=str,
        help='Name of the mapping to update (e.g., azure-core-http-netty)'
    )
    parser.add_argument(
        '--mapping-dir',
        type=str,
        default='.advisor/azure-sdk-for-java-mappings',
        help='Directory containing mapping files (default: .advisor/azure-sdk-for-java-mappings)'
    )
    parser.add_argument(
        '--repo-path',
        type=str,
        default='.github/repos/azure-sdk-for-java',
        help='Path to the Azure SDK repository (default: .github/repos/azure-sdk-for-java)'
    )
    parser.add_argument(
        '--effective-poms-dir',
        type=str,
        default='.github/effective-poms',
        help='Directory to store effective POMs (default: .github/effective-poms)'
    )
    parser.add_argument(
        '--extract-script',
        type=str,
        default='./scripts/extract_pom_deps.py',
        help='Path to the extract_pom_deps.py script (default: ./scripts/extract_pom_deps.py)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )
    parser.add_argument(
        '--skip-on-error',
        action='store_true',
        help='Skip versions that fail to process instead of stopping'
    )
    parser.add_argument(
        '--min-version',
        type=str,
        default=None,
        help='Minimum version to process (e.g., 1.12 to skip older versions)'
    )
    parser.add_argument(
        '--module-path',
        type=str,
        default=None,
        help='Maven module path for multi-module projects (e.g., sdk/core/azure-core-http-netty)'
    )

    return parser.parse_args()

def main():
    # Parse command line arguments
    args = parse_arguments()

    # Define paths from arguments
    mapping_file = os.path.join(args.mapping_dir, f"{args.mapping_name}.json")
    repo_path = args.repo_path
    effective_poms_dir = args.effective_poms_dir
    extract_script = args.extract_script

    print(f"Starting mapping update for: {args.mapping_name}")
    print(f"Mapping file: {mapping_file}")
    print(f"Repository path: {repo_path}")
    print(f"Effective POMs directory: {effective_poms_dir}")
    print(f"Extract script: {extract_script}")
    if args.module_path:
        print(f"Module path: {args.module_path}")

    if args.dry_run:
        print("DRY RUN MODE - No changes will be made")

    # Validate paths
    if not os.path.exists(mapping_file):
        print(f"Error: Mapping file not found: {mapping_file}")
        sys.exit(1)

    if not os.path.exists(repo_path):
        print(f"Error: Repository path not found: {repo_path}")
        sys.exit(1)

    if not os.path.exists(extract_script):
        print(f"Error: Extract script not found: {extract_script}")
        sys.exit(1)

    # Validate module path if provided
    if args.module_path:
        module_full_path = os.path.join(repo_path, args.module_path)
        if not os.path.exists(module_full_path):
            print(f"Warning: Module path does not exist: {module_full_path}")
            print(f"Maven will fail if the module is not available in the checked out tag")
        else:
            print(f"Using module path: {args.module_path}")

    # Create effective-poms directory if it doesn't exist
    os.makedirs(effective_poms_dir, exist_ok=True)

    # Read existing mapping
    print(f"\nReading existing mapping from {mapping_file}")
    mapping_data = read_json_file(mapping_file)

    # Get existing versions
    existing_versions = get_existing_versions(mapping_data)
    print(f"Existing versions: {existing_versions}")

    # Convert existing versions to major.minor tuples for comparison
    existing_major_minor = set()
    for version in existing_versions:
        # Parse versions like "1.5.x" by splitting and taking first two parts
        parts = version.replace('.x', '').split('.')
        if len(parts) >= 2:
            try:
                major = int(parts[0])
                minor = int(parts[1])
                if major > 0:
                    existing_major_minor.add((major, minor))
            except ValueError:
                print(f"Warning: Could not parse version: {version}")
                continue

    print(f"Parsed existing major.minor versions: {sorted(existing_major_minor)}")

    # Get repository tags
    tag_prefix = f"{args.mapping_name}_"
    print(f"\nGetting tags with prefix: {tag_prefix}")
    tags = get_repo_tags(repo_path, tag_prefix)
    print(f"Found {len(tags)} tags")

    if not tags or tags == ['']:
        print("No tags found with the specified prefix")
        return

    # Group tags by major.minor and get latest patch
    latest_tags = group_tags_by_major_minor(tags)
    print(f"Unique major.minor versions found in tags: {len(latest_tags)}")
    print(f"Tag versions: {sorted(latest_tags.keys())}")

    # Find missing versions
    missing_versions = []
    for (major, minor), tag in latest_tags.items():
        if (major, minor) not in existing_major_minor:
            missing_versions.append(((major, minor), tag))

    missing_versions.sort(key=lambda x: x[0])  # Sort by version

    # Apply minimum version filter if specified
    if args.min_version:
        try:
            min_parts = args.min_version.split('.')
            min_major = int(min_parts[0])
            min_minor = int(min_parts[1]) if len(min_parts) > 1 else 0
            original_count = len(missing_versions)
            missing_versions = [((maj, min_v), tag) for (maj, min_v), tag in missing_versions
                              if maj > min_major or (maj == min_major and min_v >= min_minor)]
            filtered_count = original_count - len(missing_versions)
            if filtered_count > 0:
                print(f"Filtered out {filtered_count} versions older than {args.min_version}")
        except (ValueError, IndexError):
            print(f"Warning: Invalid min-version format '{args.min_version}', ignoring filter")

    print(f"Missing versions to process: {len(missing_versions)}")

    if not missing_versions:
        print("No missing versions to process. Checking for other updates...")

        # Still check for Java version consistency and other fixes
        if 'rewrite' in mapping_data:
            changes_made = False

            # Ensure Java version consistency
            print(f"\nChecking Java version consistency...")
            java_fixes = ensure_java_version_consistency(mapping_data['rewrite'])
            if java_fixes > 0:
                print(f"  Fixed {java_fixes} entries for Spring Boot consistency")
                changes_made = True
            else:
                print(f"  All entries are already consistent")

            # Sort versions if needed
            original_order = list(mapping_data['rewrite'].keys())
            sorted_order = sort_versions(original_order)
            if original_order != sorted_order:
                print(f"\nSorting versions...")
                mapping_data['rewrite'] = sort_rewrite_dict(mapping_data['rewrite'])
                print(f"  Reordered versions for better readability")
                changes_made = True

            # Always update nextRewrite links
            print(f"\nVerifying nextRewrite links...")
            update_next_rewrite_links(mapping_data['rewrite'])
            changes_made = True

            if changes_made:
                # Write updated mapping back to file
                print(f"\nWriting updated mapping to {mapping_file}")
                write_json_file(mapping_file, mapping_data)
                print(f"\n{'='*60}")
                print("✓ Mapping file updated with consistency fixes!")
                print(f"{'='*60}")
            else:
                print("\nMapping is up to date - no changes needed!")

        return

    print(f"Missing versions: {[get_major_minor_string(m, n) for (m, n), _ in missing_versions]}")

    if args.dry_run:
        print("\nDry run complete. The following versions would be processed:")
        for (major, minor), tag in missing_versions:
            print(f"  - {get_major_minor_string(major, minor)} (tag: {tag})")
        if args.module_path:
            print(f"\nModule path to be used: {args.module_path}")
        return

    # Store current branch/tag to restore later
    current_ref_result = run_command(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=repo_path, check=False)
    original_ref = current_ref_result.stdout.strip()
    if original_ref == 'HEAD' or current_ref_result.returncode != 0:
        # We're in detached HEAD state, get the commit SHA
        current_ref_result = run_command(['git', 'rev-parse', 'HEAD'], cwd=repo_path)
        original_ref = current_ref_result.stdout.strip()

    try:
        # Process each missing version
        processed_count = 0
        skipped_count = 0

        for (major, minor), tag in missing_versions:
            version_str = get_major_minor_string(major, minor)
            print(f"\n{'='*60}")
            print(f"Processing version {version_str} (tag: {tag})")
            print(f"{'='*60}")

            try:
                # Checkout the tag
                print(f"Checking out tag: {tag}")
                checkout_tag(repo_path, tag)

                # Generate effective POM
                version_for_filename = f"{major}.{minor}.0"  # Use a consistent format
                print(f"Generating effective POM for version {version_for_filename}")
                effective_pom_path = generate_effective_pom(
                    repo_path,
                    version_for_filename,
                    effective_poms_dir,
                    args.module_path
                )

                # Extract dependencies
                print(f"Extracting dependencies from effective POM")
                extract_pom_dependencies(extract_script, effective_pom_path)

                # Read the generated version.json
                version_json_path = os.path.join(effective_poms_dir, f"{version_for_filename}.json")
                if not os.path.exists(version_json_path):
                    raise FileNotFoundError(f"Expected version JSON file not found: {version_json_path}")

                version_data = read_json_file(version_json_path)

                # Create rewrite object
                rewrite_obj = create_rewrite_object(version_data)

                # Add to mapping
                if 'rewrite' not in mapping_data:
                    mapping_data['rewrite'] = {}
                mapping_data['rewrite'][version_str] = rewrite_obj

                print(f"✓ Added version {version_str} to mapping")
                processed_count += 1

            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                if args.skip_on_error:
                    print(f"⚠ Warning: Failed to process version {version_str}: {str(e)}")
                    print(f"  Skipping this version and continuing...")
                    skipped_count += 1
                    continue
                else:
                    print(f"✗ Error: Failed to process version {version_str}")
                    print(f"  Use --skip-on-error to skip failed versions and continue")
                    raise

        # Sort the rewrite dictionary by version
        if 'rewrite' in mapping_data:
            print(f"\nPost-processing all versions...")

            # Sort versions
            print(f"  Sorting versions in mapping file...")
            original_order = list(mapping_data['rewrite'].keys())
            mapping_data['rewrite'] = sort_rewrite_dict(mapping_data['rewrite'])
            new_order = list(mapping_data['rewrite'].keys())

            if original_order != new_order:
                print(f"    Reordered versions for better readability")
                print(f"    Order: {new_order[:5]}{'...' if len(new_order) > 5 else ''}")

            # Update links
            print(f"  Updating nextRewrite links...")
            update_next_rewrite_links(mapping_data['rewrite'])

            # Ensure Java version consistency for ALL entries (new and existing)
            print(f"  Ensuring Java version consistency for all entries...")
            java_fixes = ensure_java_version_consistency(mapping_data['rewrite'])
            if java_fixes > 0:
                print(f"    Fixed {java_fixes} entries for Spring Boot consistency")
            else:
                print(f"    All entries are already consistent")

        # Write updated mapping back to file
        print(f"\nWriting updated mapping to {mapping_file}")
        write_json_file(mapping_file, mapping_data)

        # Show final version list
        if 'rewrite' in mapping_data:
            final_versions = list(mapping_data['rewrite'].keys())
            print(f"\nFinal version list ({len(final_versions)} versions):")
            for i, version in enumerate(final_versions):
                if i < 5:
                    print(f"  {version}")
                elif i == 5:
                    print(f"  ... ({len(final_versions) - 5} more versions)")
                    break

        print(f"\n{'='*60}")
        print(f"✓ Mapping update completed!")
        print(f"  Processed: {processed_count} versions")
        if skipped_count > 0:
            print(f"  Skipped: {skipped_count} versions (due to errors)")
        print(f"{'='*60}")

    finally:
        # Restore original branch/tag
        print(f"\nRestoring original ref: {original_ref}")
        run_command(['git', 'checkout', original_ref], cwd=repo_path)

if __name__ == "__main__":
    main()