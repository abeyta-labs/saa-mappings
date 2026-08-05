import json
import os
import subprocess

def prefix_cucumber_mappings():
    # cucumber-jvm is a large multi-module repo, and create-mapping's
    # auto-discovery generates one mapping file per published module using
    # whatever name it derives for that module -- most come out already
    # prefixed (cucumber-core.json, cucumber-java.json, ...), but several
    # don't (gherkin.json, messages.json, query.json, ...). Sweep every
    # mapping this run just created and prefix any that are missing it, in
    # both the filename and the slug.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(script_dir, "..", ".."))
    mappings_dir = os.path.join(repo_root, ".advisor", "mappings")

    prefix = "cucumber-"
    renamed = 0

    for filename in sorted(os.listdir(mappings_dir)):
        if not filename.endswith(".json") or filename.startswith(prefix):
            continue

        path = os.path.join(mappings_dir, filename)
        rel_path = os.path.relpath(path, repo_root)

        # Only touch files this run's generator just created -- skip
        # anything that already existed before this run (an unrelated
        # mapping owned by another workflow) even if its name doesn't
        # happen to start with "cucumber-" either.
        head_has_file = subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{rel_path}"],
            cwd=repo_root, capture_output=True
        ).returncode == 0
        if head_has_file:
            continue

        with open(path, 'r') as file:
            data = json.load(file)

        old_slug = data.get("slug", "")
        if old_slug and not old_slug.startswith(prefix):
            data["slug"] = prefix + old_slug
            print(f"Updated slug: '{old_slug}' -> '{data['slug']}'")

        new_filename = prefix + filename
        new_path = os.path.join(mappings_dir, new_filename)

        with open(new_path, 'w') as file:
            json.dump(data, file, indent=2)
        os.remove(path)
        renamed += 1
        print(f"Renamed: {filename} -> {new_filename}")

    if renamed == 0:
        print("No mapping files needed the cucumber- prefix.")

if __name__ == "__main__":
    prefix_cucumber_mappings()
