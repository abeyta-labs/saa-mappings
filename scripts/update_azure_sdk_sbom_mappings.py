#!/usr/bin/env python3
"""
Script to update Azure SDK for Java mappings by analyzing repository tags and generating
dependency information using CycloneDX SBOMs instead of effective POMs.

This updated version uses CycloneDX for better dependency analysis including transitive dependencies.

Usage:
    python update_azure_sdk_sbom_mappings.py <mapping-name>

Example:
    python update_azure_sdk_sbom_mappings.py azure-core-http-netty
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

def detect_build_system(repo_path: str, module_paths: Optional[List[str]] = None) -> str:
    """Detect whether the project uses Maven or Gradle."""
    # If module paths are specified, check in the module directory
    check_paths = [repo_path]
    if module_paths:
        for module in module_paths:
            check_paths.append(os.path.join(repo_path, module))
    
    for path in check_paths:
        # Check for Maven
        if os.path.exists(os.path.join(path, 'pom.xml')):
            return 'maven'
        
        # Check for Gradle
        if os.path.exists(os.path.join(path, 'build.gradle')) or \
           os.path.exists(os.path.join(path, 'build.gradle.kts')):
            return 'gradle'
    
    # Default to Maven for Azure SDK
    return 'maven'

def generate_maven_sbom(repo_path: str, version: str, output_dir: str, module_paths: Optional[List[str]] = None) -> str:
    """
    Generate CycloneDX SBOM using Maven plugin.
    
    Returns path to the generated SBOM file.
    """
    output_file = os.path.join(output_dir, f"bom-{version}.json")
    
    # Build the Maven command with additional properties to capture Java version
    cmd = [
        'mvn', '-B',
        'org.cyclonedx:cyclonedx-maven-plugin:2.8.0:makeAggregateBom',
        '-DschemaVersion=1.5',
        '-DoutputFormat=json',
        f'-DoutputName=bom-{version}',
        f'-DoutputDirectory={os.path.abspath(output_dir)}',
        '-Dcyclonedx.skipNotDeployed=false',
        '-DincludeLicenseText=false',
        '-DincludeCompileScope=true',
        '-DincludeProvidedScope=false',
        '-DincludeRuntimeScope=true',
        '-DincludeSystemScope=false',
        '-DincludeTestScope=false',
        '-DincludeMetadata=true',
        '-DprojectType=library',
        '-Dorg.slf4j.simpleLogger.logFile=System.err'
    ]
    
    # Add module paths if specified
    if module_paths and len(module_paths) > 0:
        modules_str = ','.join(module_paths)
        cmd.extend(['-pl', modules_str])
        print(f"  Generating SBOM for modules: {modules_str}")
    
    try:
        run_command(cmd, cwd=repo_path)
    except subprocess.CalledProcessError as e:
        # Sometimes the SBOM is still generated even if Maven reports an error
        if os.path.exists(output_file):
            print(f"  Warning: Maven reported an error but SBOM was generated")
        else:
            # Try to run without aggregate if it fails
            print(f"  Trying alternative Maven command without aggregate...")
            cmd[2] = 'org.cyclonedx:cyclonedx-maven-plugin:2.8.0:makeBom'
            try:
                run_command(cmd, cwd=repo_path)
            except:
                raise e
    
    return output_file

def generate_gradle_sbom(repo_path: str, version: str, output_dir: str, module_paths: Optional[List[str]] = None) -> str:
    """
    Generate CycloneDX SBOM using Gradle plugin.
    
    Returns path to the generated SBOM file.
    """
    output_file = os.path.join(output_dir, f"bom-{version}.json")
    
    # For Gradle, we need to add the plugin to the build file or use it via command line
    # This is more complex and project-specific
    
    # Build the Gradle command
    gradle_wrapper = './gradlew' if os.path.exists(os.path.join(repo_path, 'gradlew')) else 'gradle'
    
    cmd = [
        gradle_wrapper,
        'cyclonedxBom',
        '--no-daemon',
        f'-PcyclonedxVersion=2.8.0',
        f'-PschemaVersion=1.5',
        f'-PoutputFormat=json',
        f'-PoutputName=bom-{version}',
        f'-PoutputDirectory={os.path.abspath(output_dir)}'
    ]
    
    if module_paths:
        # For Gradle multi-module projects
        for module in module_paths:
            module_task = f":{module.replace('/', ':')}:cyclonedxBom"
            cmd.append(module_task)
    
    try:
        run_command(cmd, cwd=repo_path)
    except subprocess.CalledProcessError as e:
        print(f"  Warning: Gradle SBOM generation failed, trying alternative approach")
        # Try alternative: generate dependencies list and convert
        # This would require additional implementation
        raise
    
    return output_file

def generate_sbom(repo_path: str, version: str, output_dir: str, module_paths: Optional[List[str]] = None, 
                  force_build_system: Optional[str] = None) -> str:
    """
    Generate CycloneDX SBOM for a given version.
    
    Automatically detects the build system (Maven/Gradle) and uses the appropriate plugin.
    """
    build_system = force_build_system or detect_build_system(repo_path, module_paths)
    
    print(f"  Using {build_system} to generate SBOM")
    
    if build_system == 'maven':
        return generate_maven_sbom(repo_path, version, output_dir, module_paths)
    elif build_system == 'gradle':
        return generate_gradle_sbom(repo_path, version, output_dir, module_paths)
    else:
        raise ValueError(f"Unsupported build system: {build_system}")

def extract_sbom_dependencies(script_path: str, sbom_file: str):
    """Run the extract_sbom_deps.py script on the SBOM."""
    sbom_dir = os.path.dirname(sbom_file)
    run_command(['python3', script_path, sbom_dir])

def get_java_version_from_sbom(sbom_file: str, verbose: bool = False) -> Optional[int]:
    """Extract Java version from the SBOM file."""
    try:
        with open(sbom_file, 'r') as f:
            sbom_data = json.load(f)
        
        # Check metadata properties
        if 'metadata' in sbom_data and 'properties' in sbom_data['metadata']:
            properties = sbom_data['metadata']['properties']
            if isinstance(properties, list):
                for prop in properties:
                    if isinstance(prop, dict) and 'name' in prop and 'value' in prop:
                        prop_name = prop['name']
                        prop_value = str(prop['value'])
                        
                        # Check various Java version property names
                        if prop_name in ['java.version', 'maven.compiler.target', 'maven.compiler.source', 
                                        'maven.compiler.release', 'java.runtime.version', 'java.target.version']:
                            if prop_value and prop_value != 'null':
                                version_str = prop_value
                                if verbose:
                                    print(f"    Found Java version in SBOM metadata[{prop_name}]: {version_str}")
                                
                                # Parse the version string
                                if version_str.startswith('1.'):
                                    return int(version_str.split('.')[1])
                                elif version_str.isdigit():
                                    return int(version_str)
                                else:
                                    # Try to extract numeric part
                                    match = re.match(r'^(\d+)', version_str)
                                    if match:
                                        return int(match.group(1))
        
        # Check tools section
        if 'metadata' in sbom_data and 'tools' in sbom_data['metadata']:
            tools = sbom_data['metadata']['tools']
            if isinstance(tools, list):
                for tool in tools:
                    if isinstance(tool, dict) and 'properties' in tool:
                        for prop in tool['properties']:
                            if isinstance(prop, dict) and 'name' in prop and 'value' in prop:
                                if 'java' in prop['name'].lower() and 'version' in prop['name'].lower():
                                    version_str = str(prop['value'])
                                    if verbose:
                                        print(f"    Found Java version in tools: {version_str}")
                                    
                                    if version_str.startswith('1.'):
                                        return int(version_str.split('.')[1])
                                    elif version_str.isdigit():
                                        return int(version_str)
                                    else:
                                        match = re.match(r'^(\d+)', version_str)
                                        if match:
                                            return int(match.group(1))
        
        return None
    except Exception as e:
        if verbose:
            print(f"    Failed to extract Java version from SBOM: {e}")
        return None

def create_rewrite_object(version_data: dict) -> dict:
    """Create a rewrite object from the version.json data."""
    java_version_raw = version_data.get("javaVersion", 11)

    # Convert to integer, handling various edge cases
    if isinstance(java_version_raw, int):
        java_version = java_version_raw
    elif isinstance(java_version_raw, str):
        if java_version_raw.isdigit():
            java_version = int(java_version_raw)
        elif java_version_raw == "unknown" or java_version_raw == "null" or not java_version_raw:
            java_version = 11  # Default
        else:
            try:
                java_version = int(java_version_raw)
            except (ValueError, TypeError):
                print(f"    Warning: Invalid Java version '{java_version_raw}', defaulting to 11")
                java_version = 11
    else:
        java_version = 11  # Default for any other type

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
            "version": None,
            "project": None
        },
        "requirements": {
            "supportedJavaVersions": supported_java_versions,
            "supportedGenerations": supported_generations,
            "excludedArtifacts": []
        }
    }
    
    # Note: transitiveDeps from version_data are available for internal use but not included
    # in the final output to maintain schema compatibility
    
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
        description='Update Azure SDK for Java mappings using CycloneDX SBOMs for dependency analysis.',
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
        '--sboms-dir',
        type=str,
        default='.github/sboms',
        help='Directory to store SBOMs (default: .github/sboms)'
    )
    parser.add_argument(
        '--extract-script',
        type=str,
        default='./scripts/extract_sbom_deps.py',
        help='Path to the extract_sbom_deps.py script (default: ./scripts/extract_sbom_deps.py)'
    )
    parser.add_argument(
        '--build-system',
        type=str,
        choices=['maven', 'gradle', 'auto'],
        default='auto',
        help='Build system to use (default: auto-detect)'
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
        nargs='+',  # Accept one or more module paths
        default=None,
        help='Maven/Gradle module path(s) for multi-module projects. Can specify multiple modules'
    )
    parser.add_argument(
        '--include-transitive',
        action='store_true',
        default=True,
        help='Include transitive dependency information in the mapping (default: True)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output for debugging'
    )

    return parser.parse_args()

def main():
    # Parse command line arguments
    args = parse_arguments()

    # Define paths from arguments
    mapping_file = os.path.join(args.mapping_dir, f"{args.mapping_name}.json")
    repo_path = args.repo_path
    sboms_dir = args.sboms_dir
    extract_script = args.extract_script

    print(f"Starting mapping update for: {args.mapping_name}")
    print(f"Mapping file: {mapping_file}")
    print(f"Repository path: {repo_path}")
    print(f"SBOMs directory: {sboms_dir}")
    print(f"Extract script: {extract_script}")
    print(f"Build system: {args.build_system}")
    print(f"Include transitive dependencies: {args.include_transitive}")
    
    if args.module_path:
        if len(args.module_path) == 1:
            print(f"Module path: {args.module_path[0]}")
        else:
            print(f"Module paths ({len(args.module_path)}): {', '.join(args.module_path)}")

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

    # Validate module paths if provided
    if args.module_path:
        missing_modules = []
        for module in args.module_path:
            module_full_path = os.path.join(repo_path, module)
            if not os.path.exists(module_full_path):
                missing_modules.append(module)

        if missing_modules:
            print(f"Warning: The following module paths do not exist:")
            for module in missing_modules:
                print(f"  - {os.path.join(repo_path, module)}")
            print(f"The build tool will fail if these modules are not available in the checked out tags")
        else:
            if len(args.module_path) == 1:
                print(f"Using module path: {args.module_path[0]}")
            else:
                print(f"Using {len(args.module_path)} module paths:")
                for module in args.module_path:
                    print(f"  - {module}")

    # Create SBOMs directory if it doesn't exist
    os.makedirs(sboms_dir, exist_ok=True)

    # Read existing mapping
    print(f"\nReading existing mapping from {mapping_file}")
    mapping_data = read_json_file(mapping_file)

    # Get existing versions
    existing_versions = get_existing_versions(mapping_data)
    print(f"Existing versions: {existing_versions}")

    # Convert existing versions to major.minor tuples for comparison
    existing_major_minor = set()
    for version in existing_versions:
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
            if len(args.module_path) == 1:
                print(f"\nModule path to be used: {args.module_path[0]}")
            else:
                print(f"\nModule paths to be used ({len(args.module_path)}):")
                for module in args.module_path:
                    print(f"  - {module}")
        return

    # Store current branch/tag to restore later
    current_ref_result = run_command(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=repo_path, check=False)
    original_ref = current_ref_result.stdout.strip()
    if original_ref == 'HEAD' or current_ref_result.returncode != 0:
        # We're in detached HEAD state, get the commit SHA
        current_ref_result = run_command(['git', 'rev-parse', 'HEAD'], cwd=repo_path)
        original_ref = current_ref_result.stdout.strip()

    # Determine build system
    force_build_system = None if args.build_system == 'auto' else args.build_system

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

                # Generate SBOM
                version_for_filename = f"{major}.{minor}.0"  # Use a consistent format
                print(f"Generating CycloneDX SBOM for version {version_for_filename}")
                sbom_path = generate_sbom(
                    repo_path,
                    version_for_filename,
                    sboms_dir,
                    args.module_path,
                    force_build_system
                )

                # Extract dependencies from SBOM
                print(f"Extracting dependencies from SBOM")
                extract_sbom_dependencies(extract_script, sbom_path)

                # Read the generated version.json
                version_json_path = os.path.join(sboms_dir, f"{version_for_filename}.json")
                if not os.path.exists(version_json_path):
                    raise FileNotFoundError(f"Expected version JSON file not found: {version_json_path}")

                version_data = read_json_file(version_json_path)

                # Check if Java version is missing
                java_version_raw = version_data.get('javaVersion')
                needs_java_version = (
                    not java_version_raw or
                    java_version_raw == 'null' or
                    java_version_raw == 'unknown' or
                    (isinstance(java_version_raw, str) and not java_version_raw.isdigit())
                )

                if needs_java_version:
                    print(f"  Java version not found in SBOM (got: {java_version_raw}), checking SBOM metadata...")
                    java_version = get_java_version_from_sbom(sbom_path, verbose=args.verbose if hasattr(args, 'verbose') else False)
                    if java_version:
                        print(f"    Found Java version in SBOM metadata: {java_version}")
                        version_data['javaVersion'] = java_version
                        # Write the updated JSON back
                        write_json_file(version_json_path, version_data)
                    else:
                        print(f"    Could not determine Java version, using inference from dependencies...")
                        # Try to infer from dependencies
                        inferred = None
                        if 'deps' in version_data:
                            from extract_sbom_deps import infer_java_version_from_deps
                            inferred = infer_java_version_from_deps(version_data['deps'])
                        
                        if inferred:
                            print(f"    Inferred Java version {inferred} from dependencies")
                            version_data['javaVersion'] = int(inferred)
                        else:
                            print(f"    Could not infer Java version, defaulting to 11")
                            version_data['javaVersion'] = 11
                        write_json_file(version_json_path, version_data)

                # Create rewrite object
                rewrite_obj = create_rewrite_object(version_data)

                # Add to mapping
                if 'rewrite' not in mapping_data:
                    mapping_data['rewrite'] = {}
                mapping_data['rewrite'][version_str] = rewrite_obj

                print(f"✓ Added version {version_str} to mapping")
                
                # Note: transitive dependencies are tracked in version_data but not included
                # in the final mapping to maintain schema compatibility
                if 'transitiveDeps' in version_data and version_data['transitiveDeps']:
                    total_transitive = sum(len(deps) for deps in version_data['transitiveDeps'].values())
                    print(f"  (Analyzed {total_transitive} transitive dependencies for internal use)")
                
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
        print(f"✓ Mapping update completed using CycloneDX SBOMs!")
        print(f"  Processed: {processed_count} versions")
        if skipped_count > 0:
            print(f"  Skipped: {skipped_count} versions (due to errors)")
        print(f"  Transitive dependencies: {'included' if args.include_transitive else 'excluded'}")
        print(f"{'='*60}")

    finally:
        # Restore original branch/tag
        print(f"\nRestoring original ref: {original_ref}")
        run_command(['git', 'checkout', original_ref], cwd=repo_path)

if __name__ == "__main__":
    main()
