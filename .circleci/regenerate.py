#!/usr/bin/env python3

"""
This script should use a very simple, functional programming style.
Avoid Jinja macros in favor of native Python functions.

Don't go overboard on code generation; use Python only to generate
content that can't be easily declared statically using CircleCI's YAML API.

Data declarations (e.g. the nested loops for defining the configuration matrix)
should be at the top of the file for easy updating.

See this comment for design rationale:
https://github.com/pytorch/vision/pull/1321#issuecomment-531033978
"""

import os.path

import jinja2
import yaml
from jinja2 import select_autoescape


PYTHON_VERSIONS = ["3.8", "3.9", "3.10", "3.11"]

RC_PATTERN = r"/v[0-9]+(\.[0-9]+)*-rc[0-9]+/"


def build_workflows(prefix="", filter_branch=None, upload=False, indentation=6, windows_latest_only=False):
    w = []
    for btype in ["wheel", "conda"]:
        for os_type in ["linux", "macos", "win"]:
            python_versions = PYTHON_VERSIONS
            cu_versions_dict = {
                "linux": ["cpu", "cu117", "cu118", "cu121", "rocm5.2", "rocm5.3"],
                "win": ["cpu", "cu117", "cu118", "cu121"],
                "macos": ["cpu"],
            }
            cu_versions = cu_versions_dict[os_type]
            for python_version in python_versions:
                for cu_version in cu_versions:
                    # ROCm conda packages not yet supported
                    if cu_version.startswith("rocm") and btype == "conda":
                        continue
                    for unicode in [False]:
                        fb = filter_branch
                        if (
                            windows_latest_only
                            and os_type == "win"
                            and filter_branch is None
                            and (
                                python_version != python_versions[-1]
                                or (cu_version not in [cu_versions[0], cu_versions[-1]])
                            )
                        ):
                            fb = "main"
                        if not fb and (
                            os_type == "linux" and cu_version == "cpu" and btype == "wheel" and python_version == "3.8"
                        ):
                            # the fields must match the build_docs "requires" dependency
                            fb = "/.*/"

                        # Disable all Linux Wheels Workflows from CircleCI
                        if os_type == "linux" and btype == "wheel":
                            continue

                        # Disable all Macos Wheels Workflows from CircleCI.
                        if os_type == "macos" and btype == "wheel":
                            continue

                        # Disable all non-Windows Conda workflows
                        if os_type != "win" and btype == "conda":
                            continue

                        w += workflow_pair(
                            btype, os_type, python_version, cu_version, unicode, prefix, upload, filter_branch=fb
                        )

    return indent(indentation, w)


def workflow_pair(btype, os_type, python_version, cu_version, unicode, prefix="", upload=False, *, filter_branch=None):

    w = []
    unicode_suffix = "u" if unicode else ""
    base_workflow_name = f"{prefix}binary_{os_type}_{btype}_py{python_version}{unicode_suffix}_{cu_version}"

    w.append(
        generate_base_workflow(
            base_workflow_name, python_version, cu_version, unicode, os_type, btype, filter_branch=filter_branch
        )
    )

    # For the remaining py3.8 Linux Wheels job left around for the docs build,
    # we'll disable uploads.
    if os_type == "linux" and btype == "wheel":
        upload = False

    if upload:
        w.append(generate_upload_workflow(base_workflow_name, os_type, btype, cu_version, filter_branch=filter_branch))
        # disable smoke tests, they are broken and needs to be fixed
        # if filter_branch == "nightly" and os_type in ["linux", "win"]:
        #     pydistro = "pip" if btype == "wheel" else "conda"
        #     w.append(generate_smoketest_workflow(pydistro, base_workflow_name, filter_branch, python_version, os_type))

    return w


manylinux_images = {
    "cu117": "pytorch/manylinux-cuda117",
    "cu118": "pytorch/manylinux-cuda118",
    "cu121": "pytorch/manylinux-cuda121",
}


def get_manylinux_image(cu_version):
    if cu_version == "cpu":
        return "pytorch/manylinux-cpu"
    elif cu_version.startswith("cu"):
        cu_suffix = cu_version[len("cu") :]
        return f"pytorch/manylinux-cuda{cu_suffix}"
    elif cu_version.startswith("rocm"):
        rocm_suffix = cu_version[len("rocm") :]
        return f"pytorch/manylinux-rocm:{rocm_suffix}"


def get_conda_image(cu_version):
    if cu_version == "cpu":
        return "pytorch/conda-builder:cpu"
    elif cu_version.startswith("cu"):
        cu_suffix = cu_version[len("cu") :]
        return f"pytorch/conda-builder:cuda{cu_suffix}"


def generate_base_workflow(
    base_workflow_name, python_version, cu_version, unicode, os_type, btype, *, filter_branch=None
):

    d = {
        "name": base_workflow_name,
        "python_version": python_version,
        "cu_version": cu_version,
    }

    if os_type != "win" and unicode:
        d["unicode_abi"] = "1"

    if os_type != "win":
        d["wheel_docker_image"] = get_manylinux_image(cu_version)
        # ROCm conda packages not yet supported
        if "rocm" not in cu_version:
            d["conda_docker_image"] = get_conda_image(cu_version)

    if filter_branch is not None:
        d["filters"] = {
            "branches": {"only": filter_branch},
            "tags": {
                # Using a raw string here to avoid having to escape
                # anything
                "only": r"/v[0-9]+(\.[0-9]+)*-rc[0-9]+/"
            },
        }

    w = f"binary_{os_type}_{btype}"
    return {w: d}


def gen_filter_branch_tree(*branches, tags_list=None):
    filter_dict = {"branches": {"only": [b for b in branches]}}
    if tags_list is not None:
        filter_dict["tags"] = {"only": tags_list}
    return filter_dict


def generate_upload_workflow(base_workflow_name, os_type, btype, cu_version, *, filter_branch=None):
    d = {
        "name": f"{base_workflow_name}_upload",
        "context": "org-member",
        "requires": [base_workflow_name],
    }

    if btype == "wheel":
        d["subfolder"] = "" if os_type == "macos" else cu_version + "/"

    if filter_branch is not None:
        d["filters"] = {
            "branches": {"only": filter_branch},
            "tags": {
                # Using a raw string here to avoid having to escape
                # anything
                "only": r"/v[0-9]+(\.[0-9]+)*-rc[0-9]+/"
            },
        }

    return {f"binary_{btype}_upload": d}


def generate_smoketest_workflow(pydistro, base_workflow_name, filter_branch, python_version, os_type):

    required_build_suffix = "_upload"
    required_build_name = base_workflow_name + required_build_suffix

    smoke_suffix = f"smoke_test_{pydistro}"
    d = {
        "name": f"{base_workflow_name}_{smoke_suffix}",
        "requires": [required_build_name],
        "python_version": python_version,
    }

    if filter_branch:
        d["filters"] = gen_filter_branch_tree(filter_branch)

    return {f"smoke_test_{os_type}_{pydistro}": d}


def indent(indentation, data_list):
    return ("\n" + " " * indentation).join(yaml.dump(data_list, default_flow_style=False).splitlines())


if __name__ == "__main__":
    d = os.path.dirname(__file__)
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(d),
        lstrip_blocks=True,
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
        keep_trailing_newline=True,
    )

    with open(os.path.join(d, "config.yml"), "w") as f:
        f.write(
            env.get_template("config.yml.in").render(
                build_workflows=build_workflows,
            )
        )
