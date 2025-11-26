import os
import re
import sys
from datetime import datetime
from typing import Dict, Optional, Set

PROJECT_PATH = sys.argv[1]

IGNORED_FOLDERS = [
    ".idea",
    ".git",
    ".vscode",
    ".cursor",
    ".godot",
    "android",
    "ios_export",
]

# ----------------------------------------

startTime = datetime.now()

if not PROJECT_PATH.endswith("/"):
    PROJECT_PATH += "/"


def extract_protocoled_string(prefix: str, text: str) -> str:
    start_index = text.index(prefix)
    end_index = text[start_index:].index('"')
    uid = text[start_index : (start_index + end_index)]
    return uid


def format_memory(amount: int) -> str:
    if amount < 1_000:
        return f"{amount:.2f} B"
    if amount < 1_000_000:
        return f"{amount / 1000:.2f} KB"
    if amount < 1_000_000_000:
        return f"{amount / 1000_000:.2f} MB"
    if amount < 1_000_000_000_000:
        return f"{amount / 1000_000_000:.2f} GB"

    assert False, "what"


class Resource:
    def __init__(self, unique_id: str, path: str):
        self.uid = unique_id
        self.path = path.removeprefix(PROJECT_PATH)
        self.name = self.path.split("/")[-1]
        self.type = ""
        self.references: int = 0

        match path.split(".")[-1]:
            case "gd":
                self.type = "script"
            case "tres" | "res":
                self.type = "resource"
            case "tscn":
                self.type = "scene"
            case "png" | "jpg" | "webp" | "exr" | "tga" | "svg" | "dds":
                self.type = "image"
            case "otf" | "ttf":
                self.type = "font"
            case "glb" | "gltf" | "fbx" | "blend":
                self.type = "3D model"
            case "wav":
                self.type = "sound"
            case "gdshader":
                self.type = "shader"
            case "lmbake":
                self.type = "baked lightmap"
            case _:
                print("UNKNOWN RESOURCE TYPE:", path)

    def __str__(self):
        return f"<R ({self.type}) '{self.name}'>"


class Project:
    def __init__(self) -> None:
        self.main_scene_uid: str = ""
        self.classnames: Dict[str, str] = {}
        """ class_name --> UID mapping"""
        self.resources: Dict[str, Resource] = {}
        """ UID --> Scene/Script/Texture/... mapping"""


project = Project()


for root, dirs, files in os.walk(PROJECT_PATH):
    relative = root.replace(PROJECT_PATH, "")
    if any(ignored in relative for ignored in IGNORED_FOLDERS):
        dirs[:] = []
        continue

    for f in files:
        if f.endswith(".gd"):
            uid_path = os.path.join(root, f + ".uid")
            if os.path.exists(uid_path):
                with open(uid_path) as uid_file:
                    uid = uid_file.readline().strip()
                if script_resource := project.resources.get(uid):
                    script_resource.references += 1
                    continue
                project.resources[uid] = Resource(uid, os.path.join(root, f))

        elif f.endswith(".import"):
            # NOTE: Actually, .import files have `deps/source` attribute in them that points
            #       to the original file. But as far as I can tell, it's always this one
            image_path = os.path.join(root, f).removesuffix(".import")
            if not os.path.exists(image_path):
                print("Strange - import file without original file?", image_path)
                continue

            with open(os.path.join(root, f), "r") as import_file:
                while line := import_file.readline():
                    line = line.strip()
                    if line.startswith('uid="'):
                        uid = line[len('uid="') : -1]

                        if script_resource := project.resources.get(uid):
                            script_resource.references += 1
                            break
                        project.resources[uid] = Resource(uid, image_path)
                        break

        elif f.endswith(".tres") or f.endswith(".tscn"):
            with open(os.path.join(root, f), "r") as res_file:
                # This should be defined on the very first row of the file
                scene_resource: Optional[Resource] = None
                for line in res_file.readlines():
                    try:
                        if line.startswith("[gd_scene"):
                            uid = extract_protocoled_string("uid://", line)

                            if scene_resource := project.resources.get(uid):
                                scene_resource.references += 1
                                continue

                            scene_resource = Resource(uid, os.path.join(root, f))
                            project.resources[uid] = scene_resource
                        elif line.startswith("[gd_resource"):  # .tres
                            uid = extract_protocoled_string("uid://", line)

                            if scene_resource := project.resources.get(uid):
                                scene_resource.references += 1
                                continue

                            scene_resource = Resource(uid, os.path.join(root, f))
                            project.resources[uid] = scene_resource

                        elif line.startswith("[ext_resource"):
                            ext_uid = extract_protocoled_string("uid://", line)

                            if res := project.resources.get(ext_uid):
                                res.references += 1
                                continue

                            ext_path = extract_protocoled_string("res://", line)
                            ext_path = ext_path.removeprefix("res://")

                            project.resources[ext_uid] = Resource(ext_uid, ext_path)
                        elif "uid://" in line and not line.startswith(
                            "metadata/_custom_type_script"
                        ):
                            print(
                                "Unhandled UID reference in",
                                scene_resource,
                                "-",
                                line.strip(),
                            )
                    except ValueError:
                        pass

print("Collected", len(project.resources), "project.resources")

# Go over scripts, extract class names
for script_resource in project.resources.values():
    if script_resource.type != "script":
        continue

    with open(os.path.join(PROJECT_PATH, script_resource.path), "r") as script_file:
        for i in range(5):
            line = script_file.readline().strip()
            if "class_name" in line:
                cn = line[line.index("class_name") :].split()[1]
                assert cn not in project.classnames
                project.classnames[cn] = script_resource.uid
                break

# Also go over Autoloads and register their node names as class names
with open(os.path.join(PROJECT_PATH, "project.godot")) as project_file:
    autoloads_section = False
    while line := project_file.readline():
        if line.startswith("run/main_scene"):
            _, main_scene_uid = line.strip().split("=")
            main_scene_uid = main_scene_uid.replace('"', "")

        if line.startswith("["):
            if autoloads_section:
                break  # Finished autoloads section, that's what I care about
            autoloads_section = line.strip() == "[autoload]"
            continue

        if autoloads_section:
            if len(line.strip()) > 0:
                cn, file_path = line.strip().split("=")
                file_path = (
                    file_path.replace("*", "").replace('"', "").removeprefix("res://")
                )
                autoload_uid = next(
                    (r.uid for r in project.resources.values() if r.path == file_path)
                )
                assert cn not in project.classnames
                project.classnames[cn] = autoload_uid


print(f"Collected {len(project.classnames)} GDScript class_names")

# Go over scripts' contents once more and detect class name usage (regex?)

MISSING_FILES: Set[str] = set()

for script_resource in project.resources.values():
    if script_resource.type != "script":
        continue

    for cn, uid in project.classnames.items():
        if script_resource.uid == uid:
            continue  # Don't detect on yourself

        classname_detection = re.compile(r"\b" + cn + r"\b")

        with open(os.path.join(PROJECT_PATH, script_resource.path), "r") as script_file:
            for line in script_file.readlines():
                line = line.strip()
                if line.startswith("#"):
                    continue
                if re.search(classname_detection, line):
                    project.resources[uid].references += 1
                if 'load("' in line:
                    start_index = line.index("load(") + len('load("')
                    end_index = line[start_index:].index('")')
                    loaded_thing = line[start_index : (start_index + end_index)]
                    if loaded_thing.startswith("uid://"):
                        if loaded_thing in project.resources:
                            project.resources[loaded_thing].references += 1
                            # else - track INVALID (nonexistent) loads?
                    else:
                        if loaded_thing.startswith("res://"):  # Absolute path load
                            loaded_thing = loaded_thing.removeprefix("res://")
                        else:  # Load relative to the script?
                            dir_path = os.path.dirname(script_resource.path)
                            while loaded_thing.startswith("../"):
                                loaded_thing = loaded_thing.removeprefix("../")
                                dir_path = os.path.dirname(dir_path)
                            loaded_thing = os.path.join(dir_path, loaded_thing)
                        try:
                            res = next(
                                (
                                    r
                                    for r in project.resources.values()
                                    if r.path == loaded_thing
                                )
                            )
                            res.references += 1
                        except StopIteration:
                            MISSING_FILES.add(loaded_thing)

for mf in MISSING_FILES:
    print("Could not find referenced resource:", mf)

with open("safe_to_remove.txt", "w") as outfile:
    for uid, resource in project.resources.items():
        if resource.references == 0:
            outfile.write(resource.path + "\n")


print(
    "Totally orphan project.resources:",
    sum((1 for r in project.resources.values() if r.references == 0)),
    "out of",
    len(project.resources),
)
potential_savings = sum(
    (
        os.path.getsize(os.path.join(PROJECT_PATH, r.path))
        for r in project.resources.values()
        if os.path.exists(os.path.join(PROJECT_PATH, r.path))
    )
)
print("Potential savings:", format_memory(potential_savings))


print("Finished in", datetime.now() - startTime)
