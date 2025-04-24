# Copyright (c) 2022 European Union.
#
# The tool was developed with the contribution of the Joint Research Centre of the European Commission.
#
# This program is free software: you can redistribute it and/or modify it under the terms of the European Union Public
# Licence, either version 1.2 of the License, or (at your option) any later version.
# You may not use this work except in compliance with the Licence.
#
# You may obtain a copy of the Licence at: https://joinup.ec.europa.eu/collection/eupl/eupl-guidelines-faq-infographics
#
# Unless required by applicable law or agreed to in writing, software distributed under the Licence is distributed on
# an "AS IS" basis, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#
# See the Licence for the specific language governing permissions and limitations under the Licence.

"""Create a zip file with the plugin files, substituting the git version hash where needed.

This is a quick-and-dirty script which will only run properly from inside the ENCA repository.  Use at own risk!
"""

import os
import argparse
import glob
import subprocess
import zipfile
import requests
import warnings
from string import Template

def main() -> None:
    parser = argparse.ArgumentParser(prog="make_plugin_zip")
    all_platforms = ["linux-64", "osx-arm64", "win-64", "osx-64"]
    all_options = [*all_platforms, "all"]
    parser.add_argument("platform", choices=all_options)
    parsed = parser.parse_args()

    if parsed.platform == "all":
        for platform in all_platforms:
            print(f"Building plugin for {platform}")
            build_plugin(platform)
    else:
        print(f"Building plugin for {parsed.platform}")
        build_plugin(parsed.platform)


def build_plugin(platform: str) -> None:
    git_root, git_rev = (
        subprocess.run(
            ["git", "rev-parse", "--short", "--show-toplevel", "HEAD"],
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .strip("\n")
        .split("\n")
    )

    print("Git revision:", git_rev)
    print("Git root:", git_root)

    # Compile resources.qrc
    compile_resources_qrc(git_root)

    # build a wheel of enca itself
    # can also raaeplace this with getting from the repository
    ENCA_WHEEL_FILE = build_enca_wheel(git_root)

    # Pack the dependencies for enca, subprocess to pixi pack
    ENV_PACK_PATH = pack_enca_environment(git_root, platform)

    # pack pixi pack itself
    pixi_pack_extension = ""
    if platform == "win-64":
        pixi_pack_extension = ".exe"
    PIXI_PACK_VERSION = "0.2.2"
    PIXI_PACK_PATH = os.path.join(
        git_root,
        "enca_plugin",
        "src",
        "enca_plugin",
        f"pixi-pack{pixi_pack_extension}",
    )
    with warnings.catch_warnings(action="ignore"):
        download_pixi_pack(
            version=PIXI_PACK_VERSION, platform=platform, filepath=PIXI_PACK_PATH
        )
    os.chmod(PIXI_PACK_PATH, 0o777)

    # metadata.txt is a template where we want to insert the current git revision before zipping it:
    with open(
        os.path.join(git_root, "enca_plugin", "src", "enca_plugin", "metadata.txt"),
        "rt",
        encoding="utf-8",
    ) as f:
        template = Template(f.read())
        metadata = template.substitute(GIT_REVISION=git_rev)

    # only include files tracked in git for now:
    git_files = subprocess.run(
        ["git", "ls-tree", "-r", "--full-tree", "--name-only", "HEAD", "enca_plugin"],
        capture_output=True,
        text=True,
    ).stdout.strip("\n")

    plugin_filename = f"Sys4ENCA_plugin_{platform}_{git_rev}.zip"
    build_folder = os.path.join(git_root, "enca_plugin", "build")
    if not os.path.isdir(build_folder):
        os.mkdir(build_folder)
    plugin_file = os.path.join(build_folder, plugin_filename)

    with zipfile.ZipFile(plugin_file, "w") as plugin_zip:
        # metadata.txt
        plugin_zip.writestr("enca_plugin/metadata.txt", metadata)
        # resources.py (not in git history so manual)
        plugin_zip.write(
            os.path.join(git_root, "enca_plugin", "src", "enca_plugin", "resources.py"),
            "enca_plugin/resources.py",
        )
        # pixi pack
        plugin_zip.write(PIXI_PACK_PATH, f"enca_plugin/pixi-pack{pixi_pack_extension}")
        # environment
        plugin_zip.write(ENV_PACK_PATH, "enca_plugin/environment.tar")
        # enca wheel
        _, enca_wheel_name = os.path.split(ENCA_WHEEL_FILE)
        plugin_zip.write(ENCA_WHEEL_FILE, f"enca_plugin/{enca_wheel_name}")
        # everything inside the plugin
        for filename_git in git_files.split("\n"):
            if filename_git in [
                ".gitignore",
                "enca_plugin/src/enca_plugin/metadata.txt",
                "enca_plugin/src/enca_plugin/resources.qrc",
                "enca_plugin/src/make/make_plugin_release.py",
                "enca_plugin/pixi.lock",
                "enca_plugin/pyproject.toml",
            ]:  # skip these...
                continue
            filename = os.path.join(git_root, filename_git)
            if os.path.isdir(filename):  # symbolic links to directories
                zip_dir(git_root, filename_git, plugin_zip)
            else:
                print(f'adding file "{filename}"')
                plugin_zip.write(
                    filename,
                    arcname=os.path.relpath(
                        filename,
                        os.path.join(git_root, "enca_plugin", "src"),
                    ),
                )
    # some cleanup
    os.remove(PIXI_PACK_PATH)
    os.remove(ENV_PACK_PATH)
    os.remove(ENCA_WHEEL_FILE)
    print(f"Created {os.path.abspath(plugin_file)}")


def zip_dir(git_root, dirname, zipfile):
    for root, _, files in os.walk(os.path.join(git_root, dirname)):
        for file in files:
            filename = os.path.join(root, file)
            print(f'adding file "{filename}"')
            zipfile.write(
                filename,
                arcname=os.path.relpath(
                    filename,
                    os.path.join(git_root, "enca_plugin", "src"),
                ),
            )


def compile_resources_qrc(git_root: str) -> None:
    resources = os.path.join(git_root, "enca_plugin", "src", "enca_plugin", "resources")
    cmd = ["pyrcc5", resources + ".qrc", "-o", resources + ".py"]
    osgeo_root = os.getenv("OSGEO4W_ROOT")
    if osgeo_root:  # We are on windows
        # Seems the only bullet-proof way to set up the environment correctly for pyrcc5, is running o4w_env.bat first.
        cmd = [
            "cmd.exe",
            "/c",
            os.path.join(osgeo_root, "bin", "o4w_env.bat"),
            "&&",
        ] + cmd
    subprocess.run(cmd, check=True)


def build_enca_wheel(git_root: str) -> str:
    subprocess.run(
        [
            "python",
            "-m",
            "pip",
            "wheel",
            git_root,
            "--no-deps",
        ],
        check=True,
    )
    enca_wheel = glob.glob("sys4enca-*-py3-none-any.whl")
    assert (
        len(enca_wheel) == 1
    ), "Found multiple wheels or no wheels in current directory, must be 1"
    return enca_wheel[0]


def pack_enca_environment(git_root: str, platform: str) -> str:
    pack_file = os.path.join(
        git_root, "enca_plugin", "src", "enca_plugin", "environment.tar"
    )
    subprocess.run(
        [
            "pixi-pack",
            "pack",
            "--environment",
            "prod",
            "--platform",
            platform,
            f"{git_root}/pyproject.toml",
        ]
    )
    os.replace("environment.tar", pack_file)
    return pack_file


def download_pixi_pack(version: str, platform: str, filepath: str) -> None:
    REPO_OWNER = "Quantco"
    REPO_NAME = "pixi-pack"

    if version == "latest":
        api_url = (
            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
        )
    else:
        api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/tags/v{version}"

    # inside VITO network, downloading from Github can raise SSL issues, so we ignore them
    response = requests.get(api_url, verify=False)
    if response.status_code == 200:
        release_data = response.json()
    elif response.status_code == 404:
        raise Exception(f"Release with version {version} not found.")
    else:
        raise Exception(
            f"Failed to fetch release information: {response.status_code}, {response.text}"
        )

    filename = {
        "linux-64": "pixi-pack-x86_64-unknown-linux-musl",  # should work on non GNU linux systems without glibc, e.g. alpine
        "osx-arm64": "pixi-pack-aarch64-apple-darwin",
        "win-64": "pixi-pack-x86_64-pc-windows-msvc.exe",
        "osx-64": "pixi-pack-x86_64-apple-darwin",
    }[platform]

    print("Looking to download %s" % filename)
    for asset_data in release_data["assets"]:
        if asset_data["name"] == filename:
            archive_url = asset_data["browser_download_url"]
            print("Found %s" % archive_url)
            break
    else:
        raise Exception(f"Could not find a release asset matching {filename}")

    response = requests.get(archive_url, stream=True, verify=False)

    if response.status_code != 200:
        raise Exception(
            f"Failed to download archive from {archive_url}, status code: {response.status_code}"
        )

    with open(filepath, "wb") as f:
        f.write(response.content)

    print(f"Downloaded {filename} to {filepath}")


if __name__ == "__main__":
    main()
