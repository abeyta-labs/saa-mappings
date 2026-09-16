import json
import os
import subprocess

def update_jazzer_junit_mapping():
    # 1. Dynamically calculate paths relative to the sibling .advisor directory
    # Current folder: /scripts/mapping-cleanup/
    # Generated file: ../../.advisor/mappings/jazzer.json (repo-derived name)
    # Desired file:   ../../.advisor/mappings/jazzer-junit.json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(script_dir, "..", ".."))
    mappings_dir = os.path.join(repo_root, ".advisor", "mappings")
    generated_path = os.path.join(mappings_dir, "jazzer.json")
    target_path = os.path.join(mappings_dir, "jazzer-junit.json")
    generated_rel = os.path.relpath(generated_path, repo_root)

    new_slug = "jazzer-junit"
    wrong_coordinate = "com.code-intelligence:jazzer-api"

    # 2. The create-mapping workflow writes its output using the repo-derived
    # name (jazzer.json). If that's present, it's this run's freshly
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
        # jazzer repo itself rather than the jazzer-junit artifact)
        if data.get("slug") != new_slug:
            print(f"Updated slug to: '{new_slug}'")
            data["slug"] = new_slug

        # 4. Drop the spurious com.code-intelligence:jazzer-api coordinate
        # that gets auto-added from the repo, leaving only jazzer-junit
        coordinates = data.get("coordinates", [])
        if wrong_coordinate in coordinates:
            coordinates.remove(wrong_coordinate)
            print(f"Removed: '{wrong_coordinate}' from coordinates.")

        with open(target_path, 'w') as file:
            json.dump(data, file, indent=2)
        print(f"Successfully wrote: {target_path}")

        if source_path == generated_path:
            # jazzer.json is also the committed output of the separate
            # jazzer mapping (jazzer-mapping.yml). This run's generator just clobbered that in the
            # working tree with this mapping's narrower content -- if HEAD
            # already has a committed version at this path, restore it rather
            # than deleting, so the other mapping isn't lost on commit.
            head_has_file = subprocess.run(
                ["git", "cat-file", "-e", f"HEAD:{generated_rel}"],
                cwd=repo_root, capture_output=True
            ).returncode == 0

            if head_has_file:
                subprocess.run(["git", "checkout", "HEAD", "--", generated_rel], cwd=repo_root, check=True)
                print(f"Restored committed version of: {generated_path}")
            else:
                os.remove(generated_path)
                print(f"Removed stale generated file: {generated_path}")

    except json.JSONDecodeError:
        print("Error: Failed to decode JSON. Check the file format.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    update_jazzer_junit_mapping()
