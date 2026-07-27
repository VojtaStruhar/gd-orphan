# GD Orphans

> Script for detecting unused resources in Godot projects.

## Install and run

- Get [`uv`](https://docs.astral.sh/uv/) Python manager
- `uv sync`
- `uv run main.py --help`

## Config file

Instead of passing flags on the command line, copy [`config.example.toml`](config.example.toml)
to `config.toml`, fill in your project's paths, and run:

```
uv run main.py --config config.toml
```

JSON works too, with the same keys. CLI flags always override the matching key from
the config file, so you can still add one-off overrides on top of a config file.

## TODO

- Recognize BBcode in scenes and when setting `.text` in scripts. It might contain
  the `[img]` tag - thus referencing images.
- Read `.mtl` files and link referenced textures to them
