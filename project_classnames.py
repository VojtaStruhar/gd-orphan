
import json

from main import Project

with open("out/project.json") as f:
    project_json = json.load(f)
    project = Project.from_dict(project_json)

print("Project-defined class names:", sum(1 for uid in project.classnames.values() if (uid in project.resources and not project.resources[uid].path.startswith("addons"))))