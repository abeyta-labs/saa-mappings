import json
import os

def update_aws_sdk_mapping():
    # 1. Get the directory where the script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 2. Go up two levels (from mappping-cleanup to scripts, then to root)
    # and join with the filename
    file_path = os.path.join(script_dir, "..", "..", ".advisor/mappings/aws-sdk-java-v2.json")

    # Normalize the path (removes the ../..)
    file_path = os.path.normpath(file_path)

    try:
        print(f"Attempting to open: {file_path}")

        with open(file_path, 'r') as file:
            data = json.load(file)

        rewrite_data = data.get("rewrite", {})

        for version_key, version_content in rewrite_data.items():
            requirements = version_content.get("requirements", {})

            # Update Java version to 8
            java_versions = requirements.get("supportedJavaVersions")
            if java_versions and "minor" in java_versions:
                java_versions["minor"] = 8

            # Remove aws-sdk-java from supportedGenerations
            supported_gens = requirements.get("supportedGenerations")
            if supported_gens and "aws-sdk-java" in supported_gens:
                del supported_gens["aws-sdk-java"]

        # Save the updated data
        with open(file_path, 'w') as file:
            json.dump(data, file, indent=2)

        print(f"Successfully updated {file_path}")

    except FileNotFoundError:
        print(f"Error: The file was not found at {file_path}")
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    update_aws_sdk_mapping()