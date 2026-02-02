import json
import os

def update_aws_sdk_mapping():
    # 1. Calculate the file path relative to the script location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up two levels to reach the sibling directory of 'scripts'
    file_path = os.path.join(script_dir, "..", "..", ".advisor/mappings/aws-sdk-java-v2.json")

    try:
        print(f"Reading file from: {file_path}")

        with open(file_path, 'r') as file:
            data = json.load(file)

        rewrite_data = data.get("rewrite", {})

        for version_key, version_content in rewrite_data.items():
            requirements = version_content.get("requirements", {})
            if not requirements:
                continue

            # --- Task 1: Update Java minor version to 8 ---
            java_versions = requirements.get("supportedJavaVersions")
            if java_versions and "minor" in java_versions:
                java_versions["minor"] = 8

            # --- Task 2: Remove 'aws-sdk-java' from generations ---
            supported_gens = requirements.get("supportedGenerations", {})
            if "aws-sdk-java" in supported_gens:
                del supported_gens["aws-sdk-java"]

            # --- Task 3: Final Cleanup Logic ---
            # If 'spring-boot' is NOT in supportedGenerations,
            # remove the 'supportedJavaVersions' section entirely.
            if "spring-boot" not in supported_gens:
                if "supportedJavaVersions" in requirements:
                    del requirements["supportedJavaVersions"]
                    print(f"[{version_key}] Removed supportedJavaVersions (spring-boot not found)")

        # Save the modified data back to the file
        with open(file_path, 'w') as file:
            json.dump(data, file, indent=2)

        print(f"\nSuccessfully updated: {file_path}")

    except FileNotFoundError:
        print(f"Error: Could not find 'aws-sdk-mapping.json' at {file_path}")
    except json.JSONDecodeError:
        print("Error: The file is not valid JSON.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    update_aws_sdk_mapping()