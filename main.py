import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, Optional, Set, Any

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
        self.referenced_uids: Set[str] = set()

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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "path": self.path,
            "name": self.name,
            "type": self.type,
            "referenced_uids": [val for val in self.referenced_uids],
        }

class Project:
    def __init__(self) -> None:
        self.main_scene_uid: str = ""
        self.classnames: Dict[str, str] = {}
        """ class_name --> UID mapping"""
        self.resources: Dict[str, Resource] = {}
        """ UID --> Scene/Script/Texture/... mapping"""

    def save(self, filepath: str) -> None:
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "main_scene_uid": self.main_scene_uid,
            "classnames": self.classnames,
            "resources": {key: value.to_dict() for key, value in self.resources.items()}
        }

    def process_file(self, root: str, f: str) -> Optional[Resource]:
        if f.endswith(".gd") or f.endswith(".gdshader"):
            return self.process_script(root, f)
        elif f.endswith(".import"):
            return self.process_imported_file(root, f)
        elif f.endswith(".tres") or f.endswith(".tscn"):
            return self.process_scene_or_resource(root, f)
        elif os.path.exists(os.path.join(PROJECT_PATH, f + ".import")):
            print("Falling back to .import path for", f)
            return self.process_imported_file(root, f + ".import")
        else:
            #print("UNKNOWN FILE:", f)
            pass

        return None

    def process_script(self, root: str, f: str) -> Optional[Resource]:
        uid_path = os.path.join(root, f + ".uid")
        if os.path.exists(uid_path):
            with open(uid_path) as uid_file:
                script_uid = uid_file.readline().strip()

            self.resources[script_uid] = Resource(script_uid, os.path.join(root, f))
            return self.resources[script_uid]
        return None

    def process_imported_file(self, root: str, f: str) -> Optional[Resource]:
        # NOTE: Actually, .import files have `deps/source` attribute in them that points
        #       to the original file. But as far as I can tell, it's always this one
        image_path = os.path.join(root, f).removesuffix(".import")
        if not os.path.exists(image_path):
            print("Strange - import file without original file?", image_path)
            return None

        with open(os.path.join(root, f), "r") as import_file:
            while line := import_file.readline():
                line = line.strip()
                if line.startswith('uid="'):
                    imported_uid = line[len('uid="'): -1]
                    self.resources[imported_uid] = Resource(imported_uid, image_path)
                    return self.resources[imported_uid]
        return None
    def process_scene_or_resource(self, root: str, f: str) -> Optional[Resource]:
        with open(os.path.join(root, f), "r") as res_file:
            # This should be defined on the very first row of the file
            scene_resource: Optional[Resource] = None
            for line in res_file.readlines():
                try:
                    if line.startswith("[gd_scene"):
                        scene_uid = extract_protocoled_string("uid://", line)
                        scene_resource = Resource(scene_uid, os.path.join(root, f))
                        self.resources[scene_uid] = scene_resource

                    elif line.startswith("[gd_resource"):  # .tres
                        res_uid = extract_protocoled_string("uid://", line)
                        scene_resource = Resource(res_uid, os.path.join(root, f))
                        self.resources[res_uid] = scene_resource

                    elif line.startswith("[ext_resource"):
                        ext_path = extract_protocoled_string("res://", line).removeprefix("res://")
                        if "uid://" in line:
                            ext_uid = extract_protocoled_string("uid://", line)
                        elif ext_res := self.process_file(os.path.join(PROJECT_PATH, os.path.dirname(ext_path)), os.path.basename(ext_path)):
                            ext_uid = ext_res.uid
                        else:
                            print("Skipping external resource", line.strip())
                            continue

                        if self.resources.get(ext_uid) is None:
                            self.resources[ext_uid] = Resource(ext_uid, ext_path)

                        scene_resource.referenced_uids.add(ext_uid)

                    elif "uid://" in line and not line.startswith(
                            "metadata/_custom_type_script"
                    ):
                        rogue_uid = extract_protocoled_string("uid://", line)
                        scene_resource.referenced_uids.add(rogue_uid)
                except ValueError:
                    # `extract_protocoled_string` failed, somewhere
                    print("Substring index search failed on line:", line)

        return scene_resource

project = Project()


for root, dirs, files in os.walk(PROJECT_PATH):
    relative = root.replace(PROJECT_PATH, "")
    if any(ignored in relative for ignored in IGNORED_FOLDERS):
        dirs[:] = []
        continue

    for f in files:
        project.process_file(root, f)


print("Collected", len(project.resources), "project resources")

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
            project.main_scene_uid = extract_protocoled_string("uid://", line)

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
                assert project.classnames.get(cn) is None
                project.classnames[cn] = autoload_uid


print(f"Collected {len(project.classnames)} GDScript class_names")

# Go over scripts' contents once more and detect class name usage (regex?)

MISSING_FILES: Set[str] = set()

for script_resource in project.resources.values():
    if script_resource.type != "script":
        continue

    for cn, classname_uid in project.classnames.items():
        if script_resource.uid == classname_uid:
            continue  # Don't detect on yourself

        classname_detection = re.compile(r"\b" + cn + r"\b")

        with open(os.path.join(PROJECT_PATH, script_resource.path), "r") as script_file:
            for line in script_file.readlines():
                line = line.strip()
                if line.startswith("#"):
                    continue
                if re.search(classname_detection, line):
                    script_resource.referenced_uids.add(classname_uid)
                if 'load("' in line:
                    start_index = line.index("load(") + len('load("')
                    end_index = line[start_index:].index('")')
                    loaded_thing = line[start_index : (start_index + end_index)]
                    if loaded_thing.startswith("uid://"):
                        if referenced := project.resources.get(loaded_thing):
                            script_resource.referenced_uids.add(referenced.uid)
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
                            script_resource.referenced_uids.add(res.uid)
                        except StopIteration:
                            MISSING_FILES.add(loaded_thing)

for mf in MISSING_FILES:
    print("Could not find referenced resource:", mf)



print("Finished in", datetime.now() - startTime)

project.save("project.json")