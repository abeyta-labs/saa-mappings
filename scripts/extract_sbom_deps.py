#!/usr/bin/env python3
"""
Script to extract dependency information from CycloneDX SBOMs (Software Bill of Materials)
instead of Maven effective POMs. Supports both JSON and XML format SBOMs.

This script processes CycloneDX SBOMs and extracts dependency information including
transitive dependencies, which provides more comprehensive dependency analysis.
"""

import json
import xml.etree.ElementTree as ET
import glob
import os
import re
import sys
import argparse
from typing import Dict, Set, Tuple, Optional, List

# Dependency mappings - same as before
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
    "spring-cloud-azure": ("com.azure.spring", None),
    "spring-cloud-commons": ("org.springframework.cloud", "spring-cloud-commons"),
    "spring-cloud-function": ("org.springframework.cloud", r"^spring-cloud-function-.*"),
    "spring-cloud-stream": ("org.springframework.cloud", "spring-cloud-stream"),
    "spring-cloud-bus": ("org.springframework.cloud", "spring-cloud-bus"),
    "spring-cloud-sleuth": ("org.springframework.cloud", "spring-cloud-sleuth"),
    "azure-sdk-for-java": ("com.azure", "azure-core"),
    "azure-core-http-netty": ("com.azure", "azure-core-http-netty"),
    "micrometer": ("io.micrometer", "micrometer-core"),
    "reactor": ("io.projectreactor", "reactor-core"),
    "reactor-netty": ("io.projectreactor.netty", r"^reactor-netty.*"),
    "jedis": ("redis.clients", "jedis")
}

def normalize_java_version(version_str):
    """Normalize Java version string to an integer for comparison."""
    if not version_str:
        return 0

    version_str = str(version_str).strip()

    # Handle "1.x" format (e.g., "1.8" -> 8)
    if version_str.startswith("1."):
        try:
            return int(version_str.split(".")[1])
        except (IndexError, ValueError):
            return 0

    # Handle direct version numbers
    try:
        return int(re.match(r'^\d+', version_str).group())
    except (AttributeError, ValueError):
        return 0

def version_to_x(version_str):
    """Convert version like 1.2.3 to 1.2.x"""
    if not version_str:
        return version_str
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

        v1_parts = [int(x) for x in v1_clean.split('.') if x.isdigit()]
        v2_parts = [int(x) for x in v2_clean.split('.') if x.isdigit()]

        max_len = max(len(v1_parts), len(v2_parts))
        v1_parts.extend([0] * (max_len - len(v1_parts)))
        v2_parts.extend([0] * (max_len - len(v2_parts)))

        for p1, p2 in zip(v1_parts, v2_parts):
            if p1 > p2:
                return v1
            elif p1 < p2:
                return v2

        return v1
    except:
        return max(v1, v2)

def extract_deps_from_json_sbom(sbom_data: dict, include_transitive: bool = True, verbose: bool = False) -> Tuple[Optional[str], Dict[str, str], Dict[str, Set[str]]]:
    """
    Extract dependencies from a CycloneDX JSON SBOM.
    
    Returns:
        - Java version (if found)
        - Direct dependencies matching our patterns
        - Transitive dependency tree (dep_name -> set of transitive deps)
    """
    java_version = None
    matched_deps = {}
    transitive_deps = {}
    
    # Extract metadata for Java version - check multiple possible locations
    if 'metadata' in sbom_data:
        metadata = sbom_data['metadata']
        
        # Check properties array
        if 'properties' in metadata and isinstance(metadata['properties'], list):
            for prop in metadata['properties']:
                if isinstance(prop, dict) and 'name' in prop and 'value' in prop:
                    prop_name = prop['name']
                    prop_value = str(prop['value'])
                    
                    # Check various Java version property names
                    if prop_name in ['java.version', 'maven.compiler.target', 'maven.compiler.source', 
                                     'maven.compiler.release', 'java.runtime.version', 'java.target.version']:
                        if prop_value and prop_value != 'null':
                            java_version = prop_value
                            if verbose:
                                print(f"    Found Java version in metadata.properties[{prop_name}]: {java_version}")
                            break
        
        # Also check tools section for build tool information
        if java_version is None and 'tools' in metadata:
            tools = metadata['tools']
            if isinstance(tools, list):
                for tool in tools:
                    if isinstance(tool, dict) and 'name' in tool:
                        # Check if tool has properties with Java version
                        if 'properties' in tool and isinstance(tool['properties'], list):
                            for prop in tool['properties']:
                                if isinstance(prop, dict) and 'name' in prop and 'value' in prop:
                                    if 'java' in prop['name'].lower() and 'version' in prop['name'].lower():
                                        java_version = str(prop['value'])
                                        if verbose:
                                            print(f"    Found Java version in tool properties: {java_version}")
                                        break
                    if java_version:
                        break
    
    # Build a map of all components by their BOM ref
    components_by_ref = {}
    if 'components' in sbom_data:
        for component in sbom_data['components']:
            if 'bom-ref' in component:
                components_by_ref[component['bom-ref']] = component
    
    # Build dependency tree
    dependency_tree = {}
    if 'dependencies' in sbom_data:
        for dep_entry in sbom_data['dependencies']:
            if 'ref' in dep_entry:
                parent_ref = dep_entry['ref']
                if 'dependsOn' in dep_entry:
                    dependency_tree[parent_ref] = dep_entry['dependsOn']
    
    # Process components
    if 'components' in sbom_data:
        for component in sbom_data['components']:
            # Skip components that aren't libraries
            if component.get('type') != 'library':
                continue
                
            group = component.get('group', '')
            name = component.get('name', '')
            version = component.get('version', '')
            scope = component.get('scope', 'required')
            
            # Check if this is a compile/runtime dependency (not test/provided)
            if scope in ['excluded', 'optional']:
                continue
            
            # Check against our mappings
            for dep_name, (target_group, artifact_pattern) in dep_mappings.items():
                if group == target_group:
                    matched = False
                    
                    if artifact_pattern is None:
                        matched = True
                    elif artifact_pattern.startswith('^'):
                        if re.match(artifact_pattern, name):
                            matched = True
                    else:
                        if name == artifact_pattern:
                            matched = True
                    
                    if matched:
                        if verbose:
                            print(f"    Matched {dep_name}: {group}:{name}:{version}")
                        
                        # Store or update with higher version
                        if dep_name in matched_deps:
                            matched_deps[dep_name] = compare_versions(matched_deps[dep_name], version)
                        else:
                            matched_deps[dep_name] = version
                        
                        # Find transitive dependencies if requested
                        if include_transitive and 'bom-ref' in component:
                            transitive_refs = set()
                            _collect_transitive_deps(component['bom-ref'], dependency_tree, transitive_refs)
                            
                            # Convert refs to actual dependency info
                            transitive_names = set()
                            for ref in transitive_refs:
                                if ref in components_by_ref:
                                    trans_comp = components_by_ref[ref]
                                    trans_group = trans_comp.get('group', '')
                                    trans_name = trans_comp.get('name', '')
                                    trans_version = trans_comp.get('version', '')
                                    if trans_group and trans_name:
                                        transitive_names.add(f"{trans_group}:{trans_name}:{trans_version}")
                            
                            if transitive_names:
                                transitive_deps[dep_name] = transitive_names
                        
                        break
    
    # Infer Java version from dependencies if not found
    if java_version is None and matched_deps:
        java_version = infer_java_version_from_deps(matched_deps)
        if java_version and verbose:
            print(f"    Inferred Java version from dependencies: {java_version}")
    
    return java_version, matched_deps, transitive_deps

def extract_deps_from_xml_sbom(sbom_file: str, include_transitive: bool = True, verbose: bool = False) -> Tuple[Optional[str], Dict[str, str], Dict[str, Set[str]]]:
    """
    Extract dependencies from a CycloneDX XML SBOM.
    """
    try:
        tree = ET.parse(sbom_file)
        root = tree.getroot()
        
        # Handle CycloneDX namespace
        ns = {'cdx': 'http://cyclonedx.org/schema/bom/1.5'}
        if root.tag.startswith('{'):
            ns_match = re.match(r'\{(.*?)\}', root.tag)
            if ns_match:
                ns = {'cdx': ns_match.group(1)}
        
        java_version = None
        matched_deps = {}
        transitive_deps = {}
        
        # Extract metadata properties for Java version - check multiple locations
        for prop in root.findall('.//cdx:metadata/cdx:properties/cdx:property', ns):
            name_elem = prop.find('cdx:name', ns)
            value_elem = prop.find('cdx:value', ns)
            if name_elem is not None and value_elem is not None:
                prop_name = name_elem.text
                prop_value = value_elem.text
                if prop_name in ['java.version', 'maven.compiler.target', 'maven.compiler.source', 
                                 'maven.compiler.release', 'java.runtime.version', 'java.target.version']:
                    if prop_value and prop_value != 'null':
                        java_version = prop_value
                        if verbose:
                            print(f"    Found Java version in metadata.properties[{prop_name}]: {java_version}")
                        break
        
        # Also check in tools section
        if java_version is None:
            for tool in root.findall('.//cdx:metadata/cdx:tools/cdx:tool', ns):
                for prop in tool.findall('.//cdx:properties/cdx:property', ns):
                    name_elem = prop.find('cdx:name', ns)
                    value_elem = prop.find('cdx:value', ns)
                    if name_elem is not None and value_elem is not None:
                        if 'java' in name_elem.text.lower() and 'version' in name_elem.text.lower():
                            if value_elem.text and value_elem.text != 'null':
                                java_version = value_elem.text
                                if verbose:
                                    print(f"    Found Java version in tools: {java_version}")
                                break
                if java_version:
                    break
        
        # Build components map
        components_by_ref = {}
        for component in root.findall('.//cdx:component', ns):
            bom_ref = component.get('bom-ref')
            if bom_ref:
                components_by_ref[bom_ref] = component
        
        # Build dependency tree
        dependency_tree = {}
        for dep in root.findall('.//cdx:dependency', ns):
            ref = dep.get('ref')
            if ref:
                depends_on = []
                for child_dep in dep.findall('cdx:dependency', ns):
                    child_ref = child_dep.get('ref')
                    if child_ref:
                        depends_on.append(child_ref)
                dependency_tree[ref] = depends_on
        
        # Process components
        for component in root.findall('.//cdx:component[@type="library"]', ns):
            group_elem = component.find('cdx:group', ns)
            name_elem = component.find('cdx:name', ns)
            version_elem = component.find('cdx:version', ns)
            scope_elem = component.find('cdx:scope', ns)
            
            if group_elem is None or name_elem is None:
                continue
            
            group = group_elem.text or ''
            name = name_elem.text or ''
            version = version_elem.text if version_elem is not None else ''
            scope = scope_elem.text if scope_elem is not None else 'required'
            
            # Skip non-compile dependencies
            if scope in ['excluded', 'optional']:
                continue
            
            # Check against mappings
            for dep_name, (target_group, artifact_pattern) in dep_mappings.items():
                if group == target_group:
                    matched = False
                    
                    if artifact_pattern is None:
                        matched = True
                    elif artifact_pattern.startswith('^'):
                        if re.match(artifact_pattern, name):
                            matched = True
                    else:
                        if name == artifact_pattern:
                            matched = True
                    
                    if matched:
                        if verbose:
                            print(f"    Matched {dep_name}: {group}:{name}:{version}")
                        
                        if dep_name in matched_deps:
                            matched_deps[dep_name] = compare_versions(matched_deps[dep_name], version)
                        else:
                            matched_deps[dep_name] = version
                        
                        # Handle transitive dependencies
                        if include_transitive:
                            bom_ref = component.get('bom-ref')
                            if bom_ref:
                                transitive_refs = set()
                                _collect_transitive_deps(bom_ref, dependency_tree, transitive_refs)
                                
                                transitive_names = set()
                                for ref in transitive_refs:
                                    if ref in components_by_ref:
                                        trans_comp = components_by_ref[ref]
                                        trans_group_elem = trans_comp.find('cdx:group', ns)
                                        trans_name_elem = trans_comp.find('cdx:name', ns)
                                        trans_version_elem = trans_comp.find('cdx:version', ns)
                                        
                                        if trans_group_elem is not None and trans_name_elem is not None:
                                            trans_group = trans_group_elem.text or ''
                                            trans_name = trans_name_elem.text or ''
                                            trans_version = trans_version_elem.text if trans_version_elem is not None else ''
                                            transitive_names.add(f"{trans_group}:{trans_name}:{trans_version}")
                                
                                if transitive_names:
                                    transitive_deps[dep_name] = transitive_names
                        
                        break
        
        # Infer Java version if needed
        if java_version is None and matched_deps:
            java_version = infer_java_version_from_deps(matched_deps)
            if java_version and verbose:
                print(f"    Inferred Java version from dependencies: {java_version}")
        
        return java_version, matched_deps, transitive_deps
        
    except Exception as e:
        if verbose:
            print(f"    Error parsing XML SBOM: {e}")
            import traceback
            traceback.print_exc()
        return None, {}, {}

def _collect_transitive_deps(ref: str, dependency_tree: dict, result: set, visited: set = None):
    """Recursively collect transitive dependencies."""
    if visited is None:
        visited = set()
    
    if ref in visited:
        return
    
    visited.add(ref)
    
    if ref in dependency_tree:
        for child_ref in dependency_tree[ref]:
            result.add(child_ref)
            _collect_transitive_deps(child_ref, dependency_tree, result, visited)

def infer_java_version_from_deps(deps: Dict[str, str]) -> Optional[str]:
    """Infer Java version based on dependency versions."""
    if not deps:
        return None

    # Check Spring Boot version first (most reliable indicator)
    if 'spring-boot' in deps:
        boot_version = deps['spring-boot']
        # Spring Boot 3.2+ requires Java 17 minimum, 21 recommended
        if boot_version.startswith('3.2') or boot_version.startswith('3.3'):
            return "17"  # Could be 21, but 17 is minimum
        # Spring Boot 3.0-3.1 requires Java 17+
        elif boot_version.startswith('3.'):
            return "17"
        # Spring Boot 2.7.x supports Java 8-17, typically uses 11
        elif boot_version.startswith('2.7'):
            return "11"
        # Spring Boot 2.5-2.6 commonly uses Java 11
        elif boot_version.startswith('2.5') or boot_version.startswith('2.6'):
            return "11"
        # Spring Boot 2.0-2.4 supports Java 8
        elif boot_version.startswith('2.'):
            return "8"

    # Check Spring Framework version as fallback
    if 'spring-framework' in deps:
        framework_version = deps['spring-framework']
        # Spring Framework 6.1+ requires Java 17+
        if framework_version.startswith('6.1'):
            return "17"
        # Spring Framework 6.0.x requires Java 17+
        elif framework_version.startswith('6.'):
            return "17"
        # Spring Framework 5.3.x supports Java 8-17, commonly 11
        elif framework_version.startswith('5.3'):
            return "11"
        # Spring Framework 5.x supports Java 8
        elif framework_version.startswith('5.'):
            return "8"

    # Check Azure SDK versions (newer versions require newer Java)
    if 'azure-sdk-for-java' in deps or 'azure-core-http-netty' in deps:
        azure_version = deps.get('azure-sdk-for-java', deps.get('azure-core-http-netty', ''))
        # Parse major version
        if azure_version:
            parts = azure_version.split('.')
            if len(parts) > 0 and parts[0].isdigit():
                major = int(parts[0])
                # Azure SDK 1.40+ typically requires Java 11
                if major >= 1 and len(parts) > 1 and parts[1].isdigit():
                    minor = int(parts[1])
                    if minor >= 40:
                        return "11"
    
    # Check Reactor version
    if 'reactor' in deps:
        reactor_version = deps['reactor']
        # Reactor 3.6+ typically used with Java 17+
        if reactor_version.startswith('3.6'):
            return "17"
        # Reactor 3.5+ typically used with Java 11+
        elif reactor_version.startswith('3.5'):
            return "11"
        # Earlier Reactor 3.x versions support Java 8
        elif reactor_version.startswith('3.'):
            return "8"

    # Default to Java 11 for modern projects (more reasonable default than 8)
    return "11"

def process_sbom_file(sbom_file: str, output_dir: str, include_transitive: bool = True, verbose: bool = False) -> dict:
    """Process a single SBOM file and return the extracted data."""
    print(f"Processing SBOM: {sbom_file}")
    
    # Determine format and extract dependencies
    java_version = None
    matched_deps = {}
    transitive_deps = {}
    
    if sbom_file.endswith('.json'):
        with open(sbom_file, 'r') as f:
            sbom_data = json.load(f)
        java_version, matched_deps, transitive_deps = extract_deps_from_json_sbom(sbom_data, include_transitive, verbose)
    elif sbom_file.endswith('.xml'):
        java_version, matched_deps, transitive_deps = extract_deps_from_xml_sbom(sbom_file, include_transitive, verbose)
    else:
        print(f"  Warning: Unknown SBOM format for {sbom_file}")
        return None
    
    # Convert versions to x format
    final_deps = {}
    for dep_name, dep_version in matched_deps.items():
        final_deps[dep_name] = version_to_x(dep_version)
    
    # Extract version from filename
    basename = os.path.basename(sbom_file)
    # Try to extract version from filename patterns like: bom-1.2.3.json, sbom-1.2.3.xml, etc.
    version_match = re.search(r'(\d+\.\d+\.\d+)', basename)
    if version_match:
        version = version_match.group(1)
    else:
        version = "unknown"
    
    version_with_x = version_to_x(version)
    
    output = {
        "version": version_with_x,
        "javaVersion": java_version or "unknown",
        "deps": final_deps
    }
    
    # Add transitive dependency information if present
    if include_transitive and transitive_deps:
        output["transitiveDeps"] = {
            dep_name: list(deps) for dep_name, deps in transitive_deps.items()
        }
        if verbose:
            for dep_name, trans in transitive_deps.items():
                print(f"  {dep_name} has {len(trans)} transitive dependencies")
    
    return output

def process_sbom_directory(sbom_dir: str, include_transitive: bool = True, verbose: bool = False, pattern: str = "*bom*.{json,xml}"):
    """Process all SBOM files in the given directory."""
    
    if not os.path.exists(sbom_dir):
        print(f"Error: Directory '{sbom_dir}' does not exist")
        return 1
    
    # Find SBOM files
    sbom_files = []
    for ext in ['json', 'xml']:
        sbom_files.extend(glob.glob(os.path.join(sbom_dir, f"*bom*.{ext}")))
        sbom_files.extend(glob.glob(os.path.join(sbom_dir, f"*sbom*.{ext}")))
    
    # Remove duplicates
    sbom_files = list(set(sbom_files))
    
    if not sbom_files:
        print(f"Warning: No SBOM files found in '{sbom_dir}'")
        print(f"  Looking for files matching pattern: {pattern}")
        return 1
    
    print(f"Processing {len(sbom_files)} SBOM files in '{sbom_dir}'...")
    
    for sbom_file in sorted(sbom_files):
        result = process_sbom_file(sbom_file, sbom_dir, include_transitive, verbose)
        
        if result:
            # Generate output filename based on version
            version = result['version'].replace('.x', '.0')
            json_file = os.path.join(sbom_dir, f"{version}.json")
            
            with open(json_file, 'w') as f:
                json.dump(result, f, indent=2)
            
            print(f"Generated: {json_file}")
            if result['deps']:
                print(f"  Found dependencies: {', '.join(result['deps'].keys())}")
            if verbose:
                print(f"  Java version: {result.get('javaVersion', 'unknown')}")
            
            if 'transitiveDeps' in result:
                total_transitive = sum(len(deps) for deps in result['transitiveDeps'].values())
                print(f"  Total transitive dependencies tracked: {total_transitive}")
    
    return 0

def main():
    parser = argparse.ArgumentParser(
        description='Extract dependency information from CycloneDX SBOMs and generate JSON files'
    )
    parser.add_argument(
        'sbom_dir',
        help='Directory containing SBOM files (*.json or *.xml) to process'
    )
    parser.add_argument(
        '--no-transitive', '-t',
        action='store_true', 
        help='Exclude transitive dependency information'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    parser.add_argument(
        '--pattern', '-p',
        default='*bom*.{json,xml}',
        help='File pattern for SBOM files (default: *bom*.{json,xml})'
    )
    
    args = parser.parse_args()
    
    include_transitive = not args.no_transitive
    
    return process_sbom_directory(args.sbom_dir, include_transitive, args.verbose, args.pattern)

if __name__ == "__main__":
    sys.exit(main())
