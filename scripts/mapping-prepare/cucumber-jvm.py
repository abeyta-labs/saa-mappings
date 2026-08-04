#!/usr/bin/env python3
"""before-mapping prep for cucumber-jvm.

cucumber-jvm commits a Maven wrapper (mvnw / mvnw.cmd, Maven Wrapper 3.3.4) but
NOT the wrapper config the script needs: its .mvn/ holds only jvm.config, with no
.mvn/wrapper/maven-wrapper.properties. mvnw reads that file to learn its
distributionUrl, so ./mvnw dies immediately in a fresh clone -- and both advisor
and the workflow's offline tag install prefer ./mvnw over mvn whenever it exists.

Deleting the two wrapper scripts makes that detection fall through to the
runner's own mvn, which builds the repo fine. The deletion only ever touches the
throwaway clone in the mapping job, never anything committed here.
"""
import os
import sys

WRAPPER_FILES = ("mvnw", "mvnw.cmd")


def remove_maven_wrapper():
    # The workflow runs before-mapping scripts from the cloned repo root and
    # exports the same path as MAPPING_WORKSPACE; honor the env var when set so
    # this is also runnable by hand from anywhere.
    workspace = os.environ.get("MAPPING_WORKSPACE") or os.getcwd()

    if not os.path.isdir(workspace):
        print(f"Error: workspace not found: {workspace}")
        return 1

    print(f"Preparing workspace: {workspace}")

    for name in WRAPPER_FILES:
        path = os.path.join(workspace, name)
        if os.path.isfile(path):
            os.remove(path)
            print(f"Removed: {name}")
        else:
            # Upstream may have dropped or fixed the wrapper -- nothing to do.
            print(f"Not present, skipping: {name}")

    return 0


if __name__ == "__main__":
    sys.exit(remove_maven_wrapper())
