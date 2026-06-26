# GD Orphans

> Script for detecting unused resources in Godot projects.

## Install and run

- Get [`uv`](https://docs.astral.sh/uv/) Python manager
- `uv sync`
- `uv run main.py --help`

## TODO

- Recognize BBcode in scenes and when setting `.text` in scripts. It might contain
  the `[img]` tag - thus referencing images.
- Read `.mtl` files and link referenced textures to them
