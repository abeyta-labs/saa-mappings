import json
import os
import subprocess

def update_jqwik_web_mapping():
    # 1. Dynamically calculate paths relative to the sibling .advisor directory
    # Current folder: /scripts/mapping-cleanup/
    # Generated file: ../../.advisor/mappings/jqwik.json (repo-derived name,
    #   also the legitimate output of the base jqwik mapping)
    # Desired file:   ../../.advisor/mappings/jqwik-web.json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(script_dir, "..", ".."))
    mappings_dir = os.path.join(repo_root, ".advisor", "mappings")
    generated_path = os.path.join(mappings_dir, "jqwik.json")
    target_path = os.path.join(mappings_dir, "jqwik-web.json")
    generated_rel = os.path.relpath(generated_path, repo_root)

    new_slug = "jqwik-web"
    wrong_coordinate = "net.jqwik:jqwik-api"

    # 2. The create-mapping workflow writes its output using the repo-derived
    # name (jqwik.json). If that's present, it's this run's freshly
    # generated file, so fold it into the renamed target.
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
        # jqwik repo itself rather than the jqwik-web artifact)
        if data.get("slug") != new_slug:
            print(f"Updated slug to: '{new_slug}'")
            data["slug"] = new_slug

        # 4. Drop the spurious net.jqwik:jqwik-api coordinate that gets
        # auto-added from the repo, leaving only jqwik-web
        coordinates = data.get("coordinates", [])
        if wrong_coordinate in coordinates:
            coordinates.remove(wrong_coordinate)
            print(f"Removed: '{wrong_coordinate}' from coordinates.")

        with open(target_path, 'w') as file:
            json.dump(data, file, indent=2)
        print(f"Successfully wrote: {target_path}")

        if source_path == generated_path:
            # jqwik.json is also the committed output of the base jqwik
            # mapping (jqwik-mapping.yml). This run's generator just
            # clobbered that in the working tree with web-only content --
            # if HEAD already has a committed version at this path, restore
            # it instead of deleting, so whatever legitimately lives there
            # (the base jqwik mapping, or still-unfixed sibling residue)
            # isn't lost when this gets committed.
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
    update_jqwik_web_mapping()
