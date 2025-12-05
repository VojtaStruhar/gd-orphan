import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, Optional, Set, Any, List
from logging_utils import logger

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
IGNORED_FILES = [
    ".DS_Store",
    ".gdignore",
    ".gitignore",
    ".gitattributes",
    "LICENSE",
]

# ----------------------------------------

setting_mermaid = False
setting_load_cached_project = False
setting_modify_export_presets = True

__sequence_key = 0

if not PROJECT_PATH.endswith("/"):
    PROJECT_PATH += "/"


def fabricate_uid(prefix: str) -> str:
    global __sequence_key
    new_uid = f"uid://{prefix}{__sequence_key}"
    __sequence_key += 1
    assert is_valid_uid(new_uid)
    return new_uid


def is_valid_uid(uid: str) -> bool:
    parts = uid.split("://")
    return len(parts) == 2 and parts[0] == "uid" and parts[1].isalnum()


def quote(s: str) -> str:
    return '"' + s + '"'


def extract_protocoled_string(prefix: str, text: str) -> str:
    start_index = text.index(prefix)
    end_index = text[start_index:].index('"')
    uid = text[start_index: (start_index + end_index)]
    return uid


regex_uid = re.compile("(uid://[a-z0-9]+)")


def extract_uid_regex(line: str) -> Optional[str]:
    m = regex_uid.search(line)
    if m:
        return m.group(0)
    return None


def format_mermaid_resource(res: Resource) -> str:
    brackets = ("[", "]")
    name = res.name
    if res.type == "script":
        brackets = ("(", ")")
        if res.uid in project.classnames.values():
            name = next((key for key, val in project.classnames.items() if val == res.uid))

    elif res.type == "resource":
        brackets = ("{{", "}}")
    elif res.type == "scene":
        brackets = ("[[", "]]")
    elif res.type == "image":
        brackets = ("([", "])")

    return f"{res.uid}{brackets[0]}{name}{brackets[1]}"


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
            case "gdshader" | "gdshaderinc":
                self.type = "shader"
            case "gdextension":
                self.type = "GDExtension"
            case "lmbake":
                self.type = "baked lightmap"
            case "bin" | "dylib" | "wasm" | "a":
                self.type = "binary?"
            case "cfg" | "json":
                self.type = "config"
            case "godot":
                self.type = "Project"
            case _:
                logger.error("UNKNOWN RESOURCE TYPE:", path)

    def abspath(self) -> str:
        return os.path.join(PROJECT_PATH, self.path)

    def __str__(self):
        return f"<R ({self.type}) '{self.name}'>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "path": self.path,
            "name": self.name,
            "type": self.type,
            "referenced_uids": sorted([val for val in self.referenced_uids]),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Resource":
        r = Resource(d["uid"], d["path"])
        assert d["name"] == r.name
        assert d["type"] == r.type
        r.referenced_uids = set(d["referenced_uids"])
        return r


class Project:
    def __init__(self) -> None:
        self._project_path: str = PROJECT_PATH
        self.project_resource: Resource = None
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
            "project_path": self._project_path,
            "main_scene_uid": self.main_scene_uid,
            "classnames": self.classnames,
            "resources": {key: value.to_dict() for key, value in self.resources.items()}
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Project":
        assert d["project_path"] == PROJECT_PATH, "The loaded Project belongs to a different godot project!"
        p = Project()
        p.main_scene_uid = d["main_scene_uid"]
        p.classnames = d["classnames"]
        p.resources = {key: Resource.from_dict(val) for key, val in d["resources"].items()}
        p.project_resource = p.resources["res://project.godot"]
        return p

    def process_file(self, root: str, f: str) -> Optional[Resource]:
        assert root.startswith(PROJECT_PATH)
        file_ext = f.split(".")[-1]
        if file_ext == "uid":
            stripped = f.removesuffix(".uid")
            if parent := self.process_file(root, stripped):
                return parent

            logger.error(f"Unhandled `.uid` file: {f}")
        elif file_ext in ("gd", "gdshader", "gdshaderinc"):
            return self.register_script(root, f)
        elif f.endswith(".gdextension"):
            return self.process_gdextension(root, f)

        elif file_ext in ("png", "jpg", "svg", "otf", "ttf", "glb", "webp", "fbx", "blend", "tga", "gltf", "exr", "wav"):
            if os.path.exists(os.path.join(PROJECT_PATH, root, f + ".import")):
                return self.register_imported_file(root, f + ".import")
            logger.warning(f"No `.import` file for {root}/{f}")

        elif f.endswith(".tres") or f.endswith(".tscn"):
            return self.process_scene_or_resource(root, f)
        elif f == "project.godot":
            self.project_resource = self.register_opaque_resource(root, f)
            return self.project_resource

        elif file_ext in ("bin", "res", "lmbake", "wasm", "a", "dylib", "dds", "json"):
            return self.register_opaque_resource(root, f)

        elif file_ext == "cfg":
            return self.process_config_file(root, f)

        elif f.endswith(".import"):
            pass  # Handled per specific resource extension

        elif file_ext == "cs":
            pass  # We don't use C#

        elif file_ext in ("md", "txt", "log", "kra", "blend1", "unwrap_cache", "tmp", "depren"):
            # depren - https://github.com/godotengine/godot/issues/96687
            pass  # Don't care

        else:
            logger.error("UNKNOWN FILE:", f)
            pass

        return None

    def register_script(self, root: str, f: str) -> Optional[Resource]:
        uid_path = os.path.join(root, f + ".uid")
        if os.path.exists(uid_path):
            with open(uid_path) as uid_file:
                script_uid = uid_file.readline().strip()

            self.resources[script_uid] = Resource(script_uid, os.path.join(root, f))
            return self.resources[script_uid]
        return None

    def register_imported_file(self, root: str, f: str) -> Optional[Resource]:
        # NOTE: Actually, .import files have `deps/source` attribute in them that points
        #       to the original file. But as far as I can tell, it's always this one
        source_path = os.path.join(root, f).removesuffix(".import")
        if not os.path.exists(source_path):
            logger.warning("Strange - import file without original file?", source_path)
            return None

        with open(os.path.join(root, f), "r") as import_file:
            while line := import_file.readline():
                line = line.strip()
                if line.startswith('uid="'):
                    imported_uid = line[len('uid="'): -1]
                    self.resources[imported_uid] = Resource(imported_uid, source_path)
                    return self.resources[imported_uid]
        return None

    def register_opaque_resource(self, root: str, f: str) -> Optional[Resource]:
        """
        Who knows what's in there? I'm creating a fake UID for this resource and hope that something will reference it
        by path.
        """
        fake_uid = os.path.join(root, f).replace(PROJECT_PATH, "res://")
        if (gdext_res := self.resources.get(fake_uid)) is None:
            gdext_res = Resource(fake_uid, os.path.join(root, f))
            self.resources[gdext_res.uid] = gdext_res
        return gdext_res

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
                        elif ext_res := self.process_file(os.path.join(PROJECT_PATH, os.path.dirname(ext_path)),
                                                          os.path.basename(ext_path)):
                            ext_uid = ext_res.uid
                        else:
                            logger.warning("Skipping external resource", line.strip())
                            continue

                        if self.resources.get(ext_uid) is None:
                            self.resources[ext_uid] = Resource(ext_uid, ext_path)

                        scene_resource.referenced_uids.add(ext_uid)

                    elif "uid://" in line:
                        rogue_uid = extract_uid_regex(line)
                        if is_valid_uid(rogue_uid):
                            scene_resource.referenced_uids.add(rogue_uid)
                        else:
                            logger.warning(f"Skipping rogue UID on line: '{line}'")
                except ValueError:
                    # Probably an inline resource
                    logger.warning("Substring index search failed on line:", line.strip())

        return scene_resource

    def process_gdextension(self, root: str, f: str) -> Optional[Resource]:
        with open(os.path.join(root, f + ".uid"), "r") as gdext_uid_file:
            gdext_uid = gdext_uid_file.readline().strip()
            if (gdext_res := self.resources.get(gdext_uid)) is None:
                gdext_res = Resource(gdext_uid, os.path.join(root, f))
                self.resources[gdext_res.uid] = gdext_res


        with open(os.path.join(root, f), "r") as res_file:
            for line in res_file.readlines():
                try:
                    if "res://" in line:
                        res_path = extract_protocoled_string("res://", line)
                        gdext_res.referenced_uids.add(res_path)

                except ValueError:
                    # Probably an inline resource
                    logger.warning("Substring index search failed on line:", line.strip())

        return gdext_res

    def process_config_file(self, root: str, f: str) -> Optional[Resource]:
        cfg_res = self.register_opaque_resource(root, f)
        with open(cfg_res.abspath(), "r") as config_file:
            for line in config_file.readlines():
                line = line.strip()
                if line.startswith("script="):
                    relative_script_path = line.removeprefix("script=").replace("\"", "")
                    script_abspath = os.path.join(root, relative_script_path)
                    script_res = self.process_file(os.path.dirname(script_abspath), os.path.basename(script_abspath))
                    cfg_res.referenced_uids.add(script_res.uid)

        return cfg_res

    def lookup_resource_by_path(self, res_path: str) -> Optional[Resource]:
        assert res_path.startswith("res://"), "supply bare project-relative path"

        if opaque_resource := self.resources.get(res_path):
            return opaque_resource

        try:
            return next(
                (
                    r
                    for r in self.resources.values()
                    if r.path == res_path.removeprefix("res://")
                )
            )
        except StopIteration:
            return None

    def get_res_path_from_relative(self, path: str, relative_to: Resource) -> str:
        """
        For when a script or a shader loads / #includes something with relative path
        """
        assert relative_to.type in ("script", "shader")
        parent = os.path.dirname(relative_to.path)
        while path.startswith("../"):
            path = path.removeprefix("../")
            parent = os.path.dirname(parent)
        return "res://" + os.path.join(parent, path)

project = Project()

if setting_load_cached_project and os.path.exists("project.json"):
    logger.info("Loading project.json")
    with open("project.json", "r") as project_file:
        data = json.load(project_file)
        project = Project.from_dict(data)
else:
    startTime = datetime.now()
    for root, dirs, files in os.walk(PROJECT_PATH):
        relative = root.replace(PROJECT_PATH, "")
        if any(ignored in relative for ignored in IGNORED_FOLDERS):
            dirs[:] = []
            continue
        for f in files:
            if f in IGNORED_FILES:
                continue
            project.process_file(root, f)

    logger.info("Collected", len(project.resources), "project resources")

    # Go over scripts, extract class names
    for script_resource in project.resources.values():
        if script_resource.type != "script":
            continue

        if not os.path.exists(script_resource.abspath()):
            logger.warning("Nonexistent script:", script_resource.name)
            continue

        with open(script_resource.abspath(), "r") as script_file:
            parent_classname = ""
            while line := script_file.readline():
                line = line.strip()
                if line.startswith("#"):
                    continue

                words = line.split()
                try:
                    if words[0] == "class_name":
                        cn = words[words.index("class_name") + 1]
                        assert cn not in project.classnames
                        project.classnames[cn] = script_resource.uid
                        parent_classname = cn
                    elif words[0] == "class":
                        cn = words[words.index("class") + 1]
                        if parent_classname:
                            cn = parent_classname + "." + cn

                        if cn not in project.classnames:
                            project.classnames[cn] = script_resource.uid
                        else:
                            logger.debug(f"Inner class {cn} of {script_resource.name} already defined in {project.resources[project.classnames[cn]].name}")

                except IndexError:
                    pass

    # project.godot
    # Also go over Autoloads and register their node names as class names
    with open(project.project_resource.abspath()) as project_file:
        autoloads_section = False
        plugins_section = False
        while line := project_file.readline():
            if line.startswith("run/main_scene"):
                project.main_scene_uid = extract_protocoled_string("uid://", line)
                project.project_resource.referenced_uids.add(project.main_scene_uid)

            if line.startswith("["):
                autoloads_section = line.strip() == "[autoload]"
                plugins_section = line.strip() == "[editor_plugins]"
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
                    project.project_resource.referenced_uids.add(autoload_uid)


            if plugins_section:
                if line.startswith("enabled="):
                    cfg_paths = [item.replace("res://", PROJECT_PATH) for item in line.split("\"") if item.startswith("res://")]
                    for config_path in cfg_paths:
                        config_res = project.process_file(os.path.dirname(config_path), os.path.basename(config_path))
                        project.project_resource.referenced_uids.add(config_res.uid)


    logger.info(f"Collected {len(project.classnames)} GDScript class_names")

    # Go over scripts' contents once more and detect class name usage (regex?)

    MISSING_FILES: Set[str] = set()

    for script_resource in project.resources.values():
        if not os.path.exists(script_resource.abspath()):
            logger.warning("Nonexistent resource:", script_resource.name)
            continue
        if script_resource.type == "script":
            for cn, classname_uid in project.classnames.items():
                if script_resource.uid == classname_uid:
                    continue  # Don't detect on yourself

                classname_detection = re.compile(r"\b" + cn + r"\b")

                with open(script_resource.abspath(), "r") as script_file:
                    for line in script_file.readlines():
                        line = line.strip()
                        if line.startswith("#"):
                            continue
                        if re.search(classname_detection, line):
                            script_resource.referenced_uids.add(classname_uid)

                        if "\"uid://" in line:
                            random_referenced_uid = extract_protocoled_string("uid://", line)
                            script_resource.referenced_uids.add(random_referenced_uid)
                        elif "\"res://" in line:
                            res_path = extract_protocoled_string("res://", line)
                            res = project.lookup_resource_by_path(res_path)
                            if res:
                                script_resource.referenced_uids.add(res.uid)
                            else:
                                #logger.warning("Invalid 'res://' reference:", line.strip())
                                MISSING_FILES.add(res_path)
                        elif "load(\"" in line: # relative path load
                            start_index = line.index("load(") + len('load("')
                            end_index = line[start_index:].index('")')
                            loaded_thing = line[start_index: (start_index + end_index)]
                            loaded_thing = project.get_res_path_from_relative(loaded_thing, script_resource)
                            res = project.lookup_resource_by_path(loaded_thing)
                            if res:
                                script_resource.referenced_uids.add(res.uid)
                            else:
                                #logger.warning("Strange load call:", line.strip())
                                MISSING_FILES.add(loaded_thing)

        elif script_resource.type == "shader":
            with open(script_resource.abspath(), "r") as shader_file:
                for line in shader_file.readlines():
                    line = line.strip()
                    if line.startswith("#include "):
                        between_quotes = line.split('"')[1]
                        if between_quotes.startswith("res://"):
                            include_res = project.lookup_resource_by_path(between_quotes.removeprefix("res://"))
                        elif between_quotes.startswith("uid://"):
                            include_res = project.resources.get(between_quotes)
                        else: # relative path lookup
                            included_path = project.get_res_path_from_relative(between_quotes, script_resource)
                            include_res = project.lookup_resource_by_path(included_path)

                        script_resource.referenced_uids.add(include_res.uid)
                        continue

    for mf in MISSING_FILES:
        logger.warning("Could not find referenced resource:", mf)

    logger.info("Finished in", datetime.now() - startTime)
    project.save("project.json")

to_explore: List[str] = [project.project_resource.uid]
explored: Set[str] = set()

while len(to_explore) > 0:
    uid = to_explore.pop()
    if resource_to_explore := project.resources.get(uid):
        for ref_uid in resource_to_explore.referenced_uids:
            if ref_uid in explored:
                continue
            to_explore.append(ref_uid)
        explored.add(uid)
    else:
        logger.warning("Referenced uid doesn't exist:", uid)

logger.debug("Resourced referenced from main:", len(explored))

unused_resources = [res for uid, res in project.resources.items() if
                    res.uid not in explored and os.path.exists(res.abspath())]
logger.info("Unused resources:", len(unused_resources))
potential_savings: int = sum([os.path.getsize(res.abspath()) for res in unused_resources])
logger.info("Potential savings:", format_memory(potential_savings))

with open("safe_to_delete.txt", "w") as safe_to_delete:
    safe_to_delete.write("\n".join(sorted([res.path for res in unused_resources])))

if setting_modify_export_presets:
    export_presets_path = os.path.join(PROJECT_PATH, "export_presets.cfg")
    logger.debug("Modifying export presets:", export_presets_path)
    export_output: List[str] = []
    with open(export_presets_path, "r") as export_presets:
        web_export_section = False
        for line in export_presets.readlines():
            line = line.strip()
            if line.startswith("name="):
                web_export_section = line.split("=")[1].replace('"', '').strip() == "Web"

            if web_export_section:
                if line.startswith("export_filter="):
                    export_output.append('export_filter="exclude"')
                    export_output.append('export_files=PackedStringArray(' + ", ".join(
                        [quote("res://" + res.path) for res in unused_resources]) + ")")
                    continue

                if line.startswith("export_files="):
                    continue

            export_output.append(line)

    with open(export_presets_path, "w") as export_presets:
        export_presets.write("\n".join(export_output))

if setting_mermaid:
    logger.info("Generating flow chart...")
    flowchart = """---
config:
  flowchart:
    curve: bumpX
---
graph LR
"""
    flowchart_lines: List[str] = []
    for res_uid in sorted(explored):
        res = project.resources.get(res_uid)
        for ref in res.referenced_uids:
            if ref in explored:
                if ref_resource := project.resources.get(ref):
                    flowchart_lines.append(
                        f"    {format_mermaid_resource(res)} --> {format_mermaid_resource(ref_resource)}\n")
                else:
                    logger.warning("Cannot include nonexistent resource in flow chart:", ref)
            # logger.debug("Don't include unexplored references in flow chart:", ref)

    flowchart += "".join(sorted(flowchart_lines))
    with open("flowchart-mermaid.txt", "w") as flowchart_file:
        flowchart_file.write(flowchart)

with open("project.json", "w") as project_file:
    json.dump(project.to_dict(), project_file, indent=4)
