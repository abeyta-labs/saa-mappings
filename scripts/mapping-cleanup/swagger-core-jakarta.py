import json
import os
import subprocess

def update_swagger_core_jakarta_mapping():
    # 1. Dynamically calculate paths relative to the sibling .advisor directory
    # Current folder: /scripts/mapping-cleanup/
    # Generated file: ../../.advisor/mappings/swagger.json (repo-derived name,
    #   also the legitimate output of the separate swagger mapping)
    # Desired file:   ../../.advisor/mappings/swagger-core-jakarta.json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(script_dir, "..", ".."))
    mappings_dir = os.path.join(repo_root, ".advisor", "mappings")
    generated_path = os.path.join(mappings_dir, "swagger.json")
    target_path = os.path.join(mappings_dir, "swagger-core-jakarta.json")
    generated_rel = os.path.relpath(generated_path, repo_root)

    new_slug = "swagger-core-jakarta"
    wrong_coordinates = {
        "io.swagger.core.v3:swagger-annotations-jakarta",
        "io.swagger.core.v3:swagger-models-jakarta",
    }

    # 2. The create-mapping workflow writes its output using the repo-derived
    # name (swagger.json). If that's present, it's this run's freshly
    # generated file, so fold it into the renamed target. The generator
    # auto-discovers the whole jakarta family (annotations-jakarta,
    # core-jakarta, models-jakarta) alongside the explicitly requested
    # swagger-core-jakarta coordinate, mirroring the legitimate swagger
    # mapping's own annotations+core+models grouping -- but
    # swagger-annotations-jakarta and swagger-models-jakarta are already
    # separately and independently owned by their own standalone mapping
    # files, so keep only swagger-core-jakarta here to avoid duplicate
    # coordinate ownership across mapping files.
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
        # swagger-core repo itself rather than the swagger-core-jakarta artifact)
        if data.get("slug") != new_slug:
            print(f"Updated slug to: '{new_slug}'")
            data["slug"] = new_slug

        # 4. Drop the coordinates already owned by the standalone
        # swagger-annotations-jakarta/swagger-models-jakarta mappings,
        # leaving only swagger-core-jakarta
        coordinates = data.get("coordinates", [])
        for wrong_coordinate in list(coordinates):
            if wrong_coordinate in wrong_coordinates:
                coordinates.remove(wrong_coordinate)
                print(f"Removed: '{wrong_coordinate}' from coordinates.")

        with open(target_path, 'w') as file:
            json.dump(data, file, indent=2)
        print(f"Successfully wrote: {target_path}")

        if source_path == generated_path:
            # swagger.json is also the committed output of the separate
            # swagger mapping (swagger-core-mapping.yml). This run's
            # generator just clobbered that in the working tree with
            # jakarta-only content -- if HEAD already has a committed
            # version at this path, restore it instead of deleting, so the
            # real swagger mapping isn't lost when this gets committed.
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
    update_swagger_core_jakarta_mapping()
