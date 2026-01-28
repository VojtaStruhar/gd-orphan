import argparse
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from gltflib import GLTF, FileResource, GLBResource

from logging_utils import logger

IGNORED_FOLDERS = [
    ".idea",
    ".git",
    ".vscode",
    ".cursor",
    ".godot",
    "android",
    "ios_export",
    "ios/plugins"
]
IGNORED_FILES = [
    ".DS_Store",
    ".gdignore",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    "LICENSE",
]
ALWAYS_INCLUDE = [
    "export_presets.cfg",
    "firebase_configs",
    "translations"
]

# ----------------------------------------

parser = argparse.ArgumentParser()
data_source = parser.add_mutually_exclusive_group(required=True)
data_source.add_argument(
    "-p", "--project", help="Path to a Godot project containing `project.godot` file."
)
data_source.add_argument(
    "--load",
    help="Load JSON project structure created by `--dump` instead of parsing the project again.",
)
parser.add_argument("--mermaid", help="Generate a mermaid flowchart into a file.")
parser.add_argument(
    "--dump", help="Location for JSON dump of the loaded project structure."
)
parser.add_argument("--always-include", type=str, help="Comma-separated list of files or directories to exclude from the 'safe to remove' list. They should stay in the project no matter what.")


def is_valid_uid(uid: str) -> bool:
    parts = uid.split("://")
    return len(parts) == 2 and parts[0] == "uid" and parts[1].isalnum()


def quote(s: str) -> str:
    return '"' + s + '"'


def extract_protocoled_string(prefix: str, text: str) -> str:
    start_index = text.index(prefix)
    end_index = text[start_index:].index('"')
    uid = text[start_index : (start_index + end_index)]
    return uid


regex_uid = re.compile("(uid://[a-z0-9]+)")


def extract_uid_regex(line: str) -> str:
    m = regex_uid.search(line)
    if m:
        return m.group(0)
    return ""


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
        assert not path.startswith("/")
        self.path = path
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
            case "bin" | "dylib" | "wasm" | "a" | "dll":
                self.type = "binary?"
            case "cfg" | "json":
                self.type = "config"
            case "godot":
                self.type = "Project"
            case _:
                logger.error("UNKNOWN RESOURCE TYPE:", path)

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
    def __init__(self, project_path) -> None:
        if not project_path.endswith("/"):
            project_path += "/"
        self.project_path: str = project_path
        self.project_resource: Optional[Resource] = None
        self.main_scene_uid: str = ""
        self.classnames: Dict[str, str] = {}
        """ class_name --> UID mapping"""
        self.resources: Dict[str, Resource] = {}
        """ UID --> Scene/Script/Texture/... mapping"""

    def save(self, filepath: str) -> None:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_path": self.project_path,
            "main_scene_uid": self.main_scene_uid,
            "classnames": self.classnames,
            "resources": {
                key: value.to_dict() for key, value in self.resources.items()
            },
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Project":
        p = Project(d["project_path"])
        p.main_scene_uid = d["main_scene_uid"]
        p.classnames = d["classnames"]
        p.resources = {
            key: Resource.from_dict(val) for key, val in d["resources"].items()
        }
        p.project_resource = p.resources["res://project.godot"]
        return p

    def process_file(self, root: str, f: str) -> Optional[Resource]:
        assert root.startswith(self.project_path), (
            f"Invalid root path? {root}, {self.project_path}"
        )
        file_ext = f.split(".")[-1]
        match file_ext:
            case "uid":
                stripped = f.removesuffix(".uid")
                if parent := self.process_file(root, stripped):
                    return parent

                logger.error(f"Unhandled `.uid` file: {f}")
            case "gd" | "gdshader" | "gdshaderinc":
                return self.register_script(root, f)
            case "gdextension":
                return self.process_gdextension(root, f)

            case "png" | "jpg" | "svg" | "otf" | "ttf" | "webp" | "fbx" | "blend" | "tga" | "exr" | "wav":

                if os.path.exists(os.path.join(self.project_path, root, f + ".import")):
                    return self.register_imported_file(root, f + ".import")
                logger.warning(f"No `.import` file for {root}/{f}")

            case "gltf" | "glb":
                if os.path.exists(os.path.join(self.project_path, root, f + ".import")):
                    gltf_resource = self.register_imported_file(root, f + ".import")
                    gltf = GLTF.load(os.path.join(self.project_path, root, f))
                    for subres in gltf.resources:
                        if isinstance(subres, FileResource):
                            uri = subres.uri
                            uri = uri.replace("%20", " ")
                            assert not uri.startswith("..")
                            #assert not "/" in uri, f"URI contains a slash: {uri}"
                            if not os.path.exists(os.path.join(root, uri)):
                                logger.warning(f"Nonexistent image {uri} in {f}")
                            else:
                                referenced_resource = self.process_file(root, uri)
                                assert referenced_resource is not None
                                #logger.debug(f"File resource {uri} in mesh {f}")
                                gltf_resource.referenced_uids.add(referenced_resource.uid)


                    self.resources[gltf_resource.uid] = gltf_resource
                    return gltf_resource

                logger.warning(f"No `.import` file for {root}/{f}")

            case "tres" | "tscn":
                return self.process_scene_or_resource(root, f)

            case "godot": # project.godot
                self.project_resource = self.register_opaque_resource(root, f)
                return self.project_resource

            case "bin" | "wasm" | "a" | "dylib" | "dds" | "json" | "dll" | "res":
                # TODO: Parse .res files
                return self.register_opaque_resource(root, f)

            case "lmbake":
                logger.warning(f"File {f} is a total blackbox. I can't know if it references anything.")
                return self.register_opaque_resource(root, f)

            case "cfg":
                return self.process_config_file(root, f)

            case "import":
                pass  # Handled per specific resource extension

            case "cs":
                pass  # We don't use C#

            case "md" | "txt" | "log" | "kra" | "blend1" | "unwrap_cache" | "tmp" | "depren":
                # depren - https://github.com/godotengine/godot/issues/96687
                pass  # Don't care
            case _:
                logger.error("UNKNOWN FILE:", f)
                pass

        return None

    def register_script(self, root: str, f: str) -> Optional[Resource]:
        uid_path = os.path.join(root, f + ".uid")
        if os.path.exists(uid_path):
            with open(uid_path) as uid_file:
                script_uid = uid_file.readline().strip()

            self.resources[script_uid] = Resource(script_uid, os.path.join(root, f).removeprefix(self.project_path))
            return self.resources[script_uid]
        return None

    def register_imported_file(self, root: str, f: str) -> Optional[Resource]:
        # NOTE: Actually, .import files have `deps/source` attribute in them that points
        #       to the original file. But as far as I can tell, it's always this one
        source_path = os.path.join(root, f).removesuffix(".import")
        if not os.path.exists(source_path):
            logger.warning("Strange - import file without original file?", source_path)
            return None
        main_res: Optional[Resource] = None
        with open(os.path.join(root, f), "r") as import_file:
            while line := import_file.readline():
                line = line.strip()
                if line.startswith('uid="') and main_res is None:
                    imported_uid = line[len('uid="') : -1]
                    main_res = Resource(imported_uid, source_path.removeprefix(self.project_path))
                    self.resources[imported_uid] = main_res
                    continue

                if "uid://" in line:
                    assert main_res
                    uid = extract_uid_regex(line)
                    assert uid
                    main_res.referenced_uids.add(uid)

        return main_res

    def register_opaque_resource(self, root: str, f: str) -> Resource:
        """
        Who knows what's in there? I'm creating a fake UID for this resource and hope that something will reference it
        by path.
        """
        res_prefixed_path = os.path.join(root, f).replace(self.project_path, "res://")
        if (gdext_res := self.resources.get(res_prefixed_path)) is None:
            gdext_res = Resource(res_prefixed_path, os.path.join(root, f).removeprefix(self.project_path))
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
                        scene_resource = Resource(scene_uid, os.path.join(root, f).removeprefix(self.project_path))
                        self.resources[scene_uid] = scene_resource

                    elif line.startswith("[gd_resource"):  # .tres
                        res_uid = extract_protocoled_string("uid://", line)
                        scene_resource = Resource(res_uid, os.path.join(root, f).removeprefix(self.project_path))
                        self.resources[res_uid] = scene_resource

                    elif line.startswith("[ext_resource"):
                        ext_path = extract_protocoled_string(
                            "res://", line
                        ).removeprefix("res://")
                        if "uid://" in line:
                            ext_uid = extract_protocoled_string("uid://", line)
                        elif ext_res := self.process_file(
                            os.path.join(self.project_path, os.path.dirname(ext_path)),
                            os.path.basename(ext_path),
                        ):
                            ext_uid = ext_res.uid
                        else:
                            logger.warning("Skipping external resource", line.strip())
                            continue

                        if self.resources.get(ext_uid) is None:
                            self.resources[ext_uid] = Resource(ext_uid, ext_path)

                        assert scene_resource
                        scene_resource.referenced_uids.add(ext_uid)

                    elif "uid://" in line:
                        assert scene_resource
                        rogue_uid = extract_uid_regex(line)
                        if is_valid_uid(rogue_uid):
                            scene_resource.referenced_uids.add(rogue_uid)
                        else:
                            logger.warning(f"Skipping rogue UID on line: '{line}'")
                except ValueError:
                    # Probably an inline resource
                    logger.warning(
                        "Substring index search failed on line:", line.strip()
                    )

        return scene_resource

    def process_gdextension(self, root: str, f: str) -> Optional[Resource]:
        with open(os.path.join(root, f + ".uid"), "r") as gdext_uid_file:
            gdext_uid = gdext_uid_file.readline().strip()
            if (gdext_res := self.resources.get(gdext_uid)) is None:
                gdext_res = Resource(gdext_uid, os.path.join(root, f).removeprefix(self.project_path))
                self.resources[gdext_res.uid] = gdext_res

        with open(os.path.join(root, f), "r") as res_file:
            for line in res_file.readlines():
                try:
                    if "res://" in line:
                        res_path = extract_protocoled_string("res://", line)
                        gdext_res.referenced_uids.add(res_path)

                except ValueError:
                    # Probably an inline resource
                    logger.warning(
                        "Substring index search failed on line:", line.strip()
                    )

        return gdext_res

    def process_config_file(self, root: str, f: str) -> Resource:
        cfg_res = self.register_opaque_resource(root, f)
        with open(os.path.join(root, f), "r") as config_file:
            for line in config_file.readlines():
                line = line.strip()
                if line.startswith("script="):
                    relative_script_path = line.removeprefix("script=").replace('"', "")
                    script_abspath = os.path.join(root, relative_script_path)
                    script_res = self.process_file(
                        os.path.dirname(script_abspath),
                        os.path.basename(script_abspath),
                    )
                    assert script_res
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

    def collect_resources(self) -> None:
        for root, dirs, files in os.walk(self.project_path):
            relative = root.replace(self.project_path, "")
            if any(ignored in relative for ignored in IGNORED_FOLDERS):
                dirs[:] = []
                continue

            for f in files:
                if f in IGNORED_FILES:
                    continue

                self.process_file(root, f)

        logger.info("Collected", len(self.resources), "project resources")

    def extract_classnames(self) -> None:
        # Go over scripts, extract class names
        for script_resource in self.resources.values():
            if script_resource.type != "script":
                continue
            abs_path = os.path.join(self.project_path, script_resource.path)
            if not os.path.exists(abs_path):
                logger.warning("Nonexistent script:", script_resource.name)
                continue

            with open(abs_path, "r") as script_file:
                parent_classname = ""
                while line := script_file.readline():
                    line = line.strip()
                    if line.startswith("#"):
                        continue

                    words = line.split()
                    try:
                        if "class_name" in words[:2]:
                            cn = words[words.index("class_name") + 1]
                            assert cn not in self.classnames
                            self.classnames[cn] = script_resource.uid
                            parent_classname = cn
                        elif "class" in words[:2]:
                            cn = words[words.index("class") + 1]
                            if parent_classname:
                                cn = parent_classname + "." + cn

                            if cn not in self.classnames:
                                self.classnames[cn] = script_resource.uid
                            else:
                                logger.debug(
                                    f"Inner class '{cn}' of {script_resource.name} already defined in {self.resources[self.classnames[cn]].name}: <<{line}>>"
                                )

                    except IndexError:
                        pass
        logger.debug("Collected", len(self.classnames), "class_names")

    def process_project_file(self) -> None:
        # project.godot
        # Also go over Autoloads and register their node names as class names
        assert self.project_resource
        with open(self.project_path + "project.godot") as project_file:
            autoloads_section = False
            plugins_section = False
            while line := project_file.readline():
                if line.startswith("run/main_scene"):
                    self.main_scene_uid = extract_protocoled_string("uid://", line)
                    self.project_resource.referenced_uids.add(self.main_scene_uid)

                if line.startswith("["):
                    autoloads_section = line.strip() == "[autoload]"
                    plugins_section = line.strip() == "[editor_plugins]"
                    continue

                if autoloads_section:
                    if len(line.strip()) > 0:
                        cn, file_path = line.strip().split("=")
                        file_path = (
                            file_path.replace("*", "")
                            .replace('"', "")
                        )
                        autoload_res = project.lookup_resource_by_path(file_path)
                        assert autoload_res
                        assert self.classnames.get(cn) is None
                        self.classnames[cn] = autoload_res.uid
                        self.project_resource.referenced_uids.add(autoload_res.uid)

                if plugins_section:
                    if line.startswith("enabled="):
                        cfg_paths = [
                            item.replace("res://", self.project_path)
                            for item in line.split('"')
                            if item.startswith("res://")
                        ]
                        for config_path in cfg_paths:
                            config_res = self.process_config_file(
                                os.path.dirname(config_path),
                                os.path.basename(config_path),
                            )

                            self.project_resource.referenced_uids.add(config_res.uid)

        logger.info(f"Processed project.godot file")

        # Go over scripts' contents once more and detect class name usage (regex?)

    def detect_class_references_and_shader_includes(self) -> None:
        MISSING_FILES: Set[str] = set()
        for script_resource in self.resources.values():
            script_abspath = os.path.join(self.project_path, script_resource.path)
            if not os.path.exists(script_abspath):
                logger.warning("Nonexistent resource:", script_resource.name)
                continue
            if script_resource.type == "script":
                for cn, classname_uid in self.classnames.items():
                    if script_resource.uid == classname_uid:
                        continue  # Don't detect on yourself

                    classname_detection = re.compile(r"\b" + cn + r"\b")

                    with open(script_abspath, "r") as script_file:
                        for line in script_file.readlines():
                            line = line.strip()
                            if line.startswith("#"):
                                continue
                            if re.search(classname_detection, line):
                                script_resource.referenced_uids.add(classname_uid)

                            if '"uid://' in line:
                                random_referenced_uid = extract_protocoled_string(
                                    "uid://", line
                                )
                                script_resource.referenced_uids.add(
                                    random_referenced_uid
                                )
                            elif '"res://' in line:
                                res_path = extract_protocoled_string("res://", line)
                                res = self.lookup_resource_by_path(res_path)
                                if res:
                                    script_resource.referenced_uids.add(res.uid)
                                else:
                                    # logger.warning("Invalid 'res://' reference:", line.strip())
                                    MISSING_FILES.add(res_path)
                            elif 'load("' in line:  # relative path load
                                start_index = line.index("load(") + len('load("')
                                end_index = line[start_index:].index('")')
                                loaded_thing = line[
                                    start_index : (start_index + end_index)
                                ]
                                loaded_thing = self.get_res_path_from_relative(
                                    loaded_thing, script_resource
                                )
                                res = self.lookup_resource_by_path(loaded_thing)
                                if res:
                                    script_resource.referenced_uids.add(res.uid)
                                else:
                                    # logger.warning("Strange load call:", line.strip())
                                    MISSING_FILES.add(loaded_thing)

            elif script_resource.type == "shader":
                with open(script_abspath, "r") as shader_file:
                    for line in shader_file.readlines():
                        line = line.strip()
                        if line.startswith("#include "):
                            between_quotes = line.split('"')[1]
                            if between_quotes.startswith("res://"):
                                include_res = self.lookup_resource_by_path(
                                    between_quotes.removeprefix("res://")
                                )
                            elif between_quotes.startswith("uid://"):
                                include_res = self.resources.get(between_quotes)
                            else:  # relative path lookup
                                included_path = self.get_res_path_from_relative(
                                    between_quotes, script_resource
                                )
                                include_res = self.lookup_resource_by_path(
                                    included_path
                                )
                            assert include_res
                            script_resource.referenced_uids.add(include_res.uid)
                            continue

        for mf in MISSING_FILES:
            logger.warning("Could not find referenced resource:", mf)

        logger.info("Finished in", datetime.now() - startTime)

    def format_mermaid_resource(self, res: Resource) -> str:
        brackets = ("[", "]")
        name = res.name
        if res.type == "script":
            brackets = ("(", ")")
            if res.uid in self.classnames.values():
                name = next(
                    (key for key, val in self.classnames.items() if val == res.uid)
                )

        elif res.type == "resource":
            brackets = ("{{", "}}")
        elif res.type == "scene":
            brackets = ("[[", "]]")
        elif res.type == "image":
            brackets = ("([", "])")

        return f"{res.uid}{brackets[0]}{name}{brackets[1]}"

    def draw_flow_chart(self, mermaid_path: str) -> None:
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
            res = self.resources.get(res_uid)
            if res is None:
                logger.warning(f"Cannot draw nonexistent resource: {res_uid}")
                continue

            for ref in res.referenced_uids:
                if ref in explored:
                    if ref_resource := self.resources.get(ref):
                        flowchart_lines.append(
                            f"    {self.format_mermaid_resource(res)} --> {self.format_mermaid_resource(ref_resource)}\n"
                        )
                    else:
                        logger.warning(
                            "Cannot include nonexistent resource in flow chart:", ref
                        )
                # logger.debug("Don't include unexplored references in flow chart:", ref)

        flowchart += "".join(sorted(flowchart_lines))
        with open(mermaid_path, "w") as flowchart_file:
            flowchart_file.write(flowchart)


if __name__ == "__main__":
    settings = parser.parse_args()
    if settings.load:
        assert os.path.exists(settings.load)

        logger.info("Loading project.json")
        with open(settings.load, "r") as project_file:
            data = json.load(project_file)
            project = Project.from_dict(data)
    else:
        project = Project(settings.project)
        startTime = datetime.now()
        project.collect_resources()
        project.extract_classnames()
        project.process_project_file()
        project.detect_class_references_and_shader_includes()

    if settings.dump:
        project.save(settings.dump)

    # Find unreferenced resources

    assert project.project_resource
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

    unused_resources = [
        res
        for uid, res in project.resources.items()
        if res.uid not in explored and os.path.exists(os.path.join(project.project_path, res.path))
    ]

    if settings.always_include:
        ALWAYS_INCLUDE += [part.strip() for part in settings.always_include.split(",") if part.strip()]

    unused_resources = [res for res in unused_resources if not any(map(res.path.startswith, ALWAYS_INCLUDE))]

    logger.info("Unused resources:", len(unused_resources))
    potential_savings: int = sum(
        [os.path.getsize(os.path.join(project.project_path, res.path)) for res in unused_resources]
    )
    logger.info("Potential savings:", format_memory(potential_savings))

    unused_paths = sorted([res.path for res in unused_resources])
    with open("safe_to_delete.txt", "w") as safe_to_delete:
        safe_to_delete.write("\n".join(unused_paths))

    with open("safe_to_delete.csv", "w") as safe_to_delete:
        safe_to_delete.write(", ".join(unused_paths))

    translation_sources = [project.resources.get(uid) for uid in explored]
    translation_sources = [res for res in translation_sources if res.type in ("scene", "script")]
    translation_sources = sorted(["res://" + res.path for res in translation_sources if not res.path.startswith("addons")])
    with open("translation_sources.txt", "w") as translation_sources_file:
        translation_sources_file.write("PackedStringArray(\"" + "\", \"".join(translation_sources) + "\")")