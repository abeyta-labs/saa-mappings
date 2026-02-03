import json
import os

def update_jolokia_mapping():
    # 1. Dynamically calculate the path to the sibling .advisor directory
    # Current folder: /scripts/mappping-cleanup/
    # Target file: ../../.advisor/mapping/jolokia.json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.normpath(os.path.join(
        script_dir, "..", "..", ".advisor", "mapping", "jolokia.json"
    ))

    target_coordinate = "org.jolokia:jolokia-support-spring"

    try:
        print(f"Opening file: {file_path}")

        with open(file_path, 'r') as file:
            data = json.load(file)

        # 2. Access the coordinates array
        coordinates = data.get("coordinates", [])

        # 3. Check if the spring support coordinate is missing
        if target_coordinate not in coordinates:
            coordinates.append(target_coordinate)
            print(f"Added: '{target_coordinate}' to coordinates.")

            # Save the updated data back to the file
            with open(file_path, 'w') as file:
                json.dump(data, file, indent=2)
            print("Successfully updated the file.")
        else:
            print(f"'{target_coordinate}' already exists. No update needed.")

    except FileNotFoundError:
        print(f"Error: The file was not found at {file_path}")
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON. Check the file format.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    update_jolokia_mapping()