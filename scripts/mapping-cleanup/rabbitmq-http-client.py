import json
import os

def update_rabbitmq_http_client_mapping():
    # 1. Dynamically calculate the path to the sibling .advisor directory
    # Current folder: /scripts/mapping-cleanup/
    # Target file: ../../.advisor/mappings/http-client.json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.normpath(os.path.join(
        script_dir, "..", "..", ".advisor", "mappings", "http-client.json"
    ))

    new_slug = "rabbitmq-http-client"

    try:
        print(f"Opening file: {file_path}")

        with open(file_path, 'r') as file:
            data = json.load(file)

        # 2. Rename the overly generic auto-generated slug
        if data.get("slug") != new_slug:
            data["slug"] = new_slug
            print(f"Updated slug to: '{new_slug}'")

            # Save the updated data back to the file
            with open(file_path, 'w') as file:
                json.dump(data, file, indent=2)
            print("Successfully updated the file.")
        else:
            print(f"Slug already '{new_slug}'. No update needed.")

    except FileNotFoundError:
        print(f"Error: The file was not found at {file_path}")
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON. Check the file format.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    update_rabbitmq_http_client_mapping()
