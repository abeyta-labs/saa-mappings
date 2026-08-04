import json
import os

def update_spring_boot_session_mapping():
    # 1. Dynamically calculate paths relative to the sibling .advisor directory
    # Current folder: /scripts/mapping-cleanup/
    # Generated file: ../../.advisor/mappings/spring-boot.json (repo-derived name)
    # Desired file:   ../../.advisor/mappings/spring-boot-session.json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    mappings_dir = os.path.normpath(os.path.join(script_dir, "..", "..", ".advisor", "mappings"))
    generated_path = os.path.join(mappings_dir, "spring-boot.json")
    target_path = os.path.join(mappings_dir, "spring-boot-session.json")

    new_slug = "spring-boot-session"
    wrong_coordinate = "org.springframework.boot:spring-boot"

    # 2. The create-mapping workflow writes its output using the repo-derived
    # name (spring-boot.json). If that's present, it's this run's freshly
    # generated file, so fold it into the renamed target and remove the
    # stale repo-derived file rather than leaving both around.
    if os.path.exists(generated_path):
        source_path = generated_path
    elif os.path.exists(target_path):
        source_path = target_path
    else:
        print(f"Error: Neither {generated_path} nor {target_path} were found")
        return

    try:
        print(f"Opening file: {source_path}")

        with open(source_path, 'r') as file:
            data = json.load(file)

        # 3. Rename the overly generic auto-generated slug (derived from the
        # spring-boot repo itself rather than the spring-boot-session artifact)
        if data.get("slug") != new_slug:
            print(f"Updated slug to: '{new_slug}'")
            data["slug"] = new_slug

        # 4. Drop the spurious org.springframework.boot:spring-boot coordinate
        # that gets auto-added from the repo, leaving only spring-boot-session
        coordinates = data.get("coordinates", [])
        if wrong_coordinate in coordinates:
            coordinates.remove(wrong_coordinate)
            print(f"Removed: '{wrong_coordinate}' from coordinates.")

        with open(target_path, 'w') as file:
            json.dump(data, file, indent=2)
        print(f"Successfully wrote: {target_path}")

        if source_path == generated_path:
            os.remove(generated_path)
            print(f"Removed stale generated file: {generated_path}")

    except json.JSONDecodeError:
        print("Error: Failed to decode JSON. Check the file format.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    update_spring_boot_session_mapping()
