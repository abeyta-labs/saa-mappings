import json
import os

def update_aws_sdk_mapping():
    # 1. Path Calculation (Sibling logic)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_dir, "..", "..", ".advisor/mappings/aws-sdk-java-v2.json")

    try:
        print(f"Reading file: {file_path}")

        with open(file_path, 'r') as file:
            data = json.load(file)

        rewrite_data = data.get("rewrite", {})

        for version_key, version_content in rewrite_data.items():
            requirements = version_content.get("requirements", {})
            next_rewrite = version_content.get("nextRewrite", {})

            # --- Task 1: Update Java minor version to 8 ---
            if requirements:
                java_versions = requirements.get("supportedJavaVersions")
                if java_versions and "minor" in java_versions:
                    java_versions["minor"] = 8

            # --- Task 2: Remove 'aws-sdk-java' from generations ---
            if requirements:
                supported_gens = requirements.get("supportedGenerations", {})
                if "aws-sdk-java" in supported_gens:
                    del supported_gens["aws-sdk-java"]

            # --- Task 3: Remove 'project' key if null in nextRewrite ---
            if next_rewrite and "project" in next_rewrite:
                if next_rewrite["project"] is None:
                    del next_rewrite["project"]

            # --- Task 4: Final Spring Boot Cleanup ---
            # If 'spring-boot' is NOT in supportedGenerations, remove supportedJavaVersions
            if requirements:
                supported_gens = requirements.get("supportedGenerations", {})
                if "spring-boot" not in supported_gens:
                    if "supportedJavaVersions" in requirements:
                        del requirements["supportedJavaVersions"]

        # Save the updated data
        with open(file_path, 'w') as file:
            json.dump(data, file, indent=2)

        print(f"Successfully updated: {file_path}")

    except FileNotFoundError:
        print(f"Error: Could not find file at {file_path}")
    except json.JSONDecodeError:
        print("Error: Invalid JSON format.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    update_aws_sdk_mapping()