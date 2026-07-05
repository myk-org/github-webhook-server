Let me explore the codebase to understand how labels and PR size thresholds are configured.

Now let me check how label colors are merged (global vs repo-level):

Now let me look at the `.github-webhook-server.yaml` for per-repo override support:

Now let me check the default size thresholds and how they map:

Now I have a complete understanding of the label and PR size configuration system. Let me write the documentation.

# Configuring Labels and PR Size Thresholds

Control which labels appear on your pull requests, customize their colors, and define how PR size categories are calculated based on total lines changed.

## Prerequisites

- A working `config.yaml` file — see [Configuring Repositories](configuring-repositories.html) for initial setup
- The webhook server receiving pull request events from GitHub

## Quick Example

Add this to your `config.yaml` to customize labels and PR size categories:

```yaml
labels:
  enabled-labels:
    - verified
    - hold
    - size
    - can-be-merged
  colors:
    hold: red
    verified: green

pr-size-thresholds:
  Tiny:
    threshold: 10
    color: lightgray
  Small:
    threshold: 50
    color: green
  Medium:
    threshold: 150
    color: orange
  Large:
    threshold: 300
    color: red
  Massive:
    threshold: inf
    color: darkred
```

This configuration enables only four label categories, sets custom colors for `hold` and `verified`, and creates five PR size buckets instead of the built-in defaults.

## Choosing Which Labels to Enable

By default, all label categories are active. To limit which labels the server manages, list only the categories you want under `enabled-labels`:

```yaml
labels:
  enabled-labels:
    - verified
    - hold
    - size
```

With this configuration, only `verified`, `hold`, and `size` labels will be added to PRs. Labels from other categories (like `wip`, `needs-rebase`, `branch`, etc.) will not be created.

### Available Label Categories

| Category | Labels Created | Description |
|---|---|---|
| `verified` | `verified` | PR has been verified/approved for merge |
| `hold` | `hold` | PR is on hold (blocks merge) |
| `wip` | `wip` | Work in progress (blocks merge) |
| `needs-rebase` | `needs-rebase` | PR branch needs rebasing |
| `has-conflicts` | `has-conflicts` | PR has merge conflicts |
| `can-be-merged` | `can-be-merged` | PR passes all checks and can be merged |
| `size` | `size/XS`, `size/S`, `size/M`, etc. | PR size based on lines changed |
| `branch` | `branch-main`, `branch-dev`, etc. | Target branch of the PR |
| `cherry-pick` | `cherry-pick-*`, `CherryPicked` | Cherry-pick tracking labels |
| `automerge` | `automerge` | PR will auto-merge when ready |

> **Note:** Review labels (`approved-*`, `lgtm-*`, `changes-requested-*`, `commented-*`) are **always enabled** and cannot be disabled. These are essential for the review workflow.

Setting `enabled-labels` to an empty list disables all configurable labels while keeping the review labels active:

```yaml
labels:
  enabled-labels: []
```

## Setting Label Colors

Customize label colors using CSS3 color names (like `red`, `green`, `blue`, `coral`, `royalblue`):

```yaml
labels:
  colors:
    hold: red
    verified: green
    wip: orange
    needs-rebase: darkred
    has-conflicts: red
    can-be-merged: limegreen
    automerge: green
```

For dynamic labels that include a username or branch name, use the prefix with a trailing hyphen:

```yaml
labels:
  colors:
    approved-: green
    lgtm-: yellowgreen
    changes-requested-: orange
    commented-: gold
    cherry-pick-: coral
    branch-: royalblue
```

This sets the color for all labels matching that prefix — for example, `approved-: green` applies to `approved-alice`, `approved-bob`, and so on.

> **Tip:** You can combine `enabled-labels` and `colors` in the same `labels` block. Colors apply to any label that gets created, whether or not you explicitly filter categories.

## Defining PR Size Thresholds

PR size is calculated as the total number of lines changed (additions + deletions). Without custom thresholds, the server uses these built-in defaults:

| Label | Lines Changed |
|---|---|
| `size/XS` | 0–19 |
| `size/S` | 20–49 |
| `size/M` | 50–99 |
| `size/L` | 100–299 |
| `size/XL` | 300–499 |
| `size/XXL` | 500+ |

To define your own categories, add a `pr-size-thresholds` block. Each entry needs a `color` (CSS3 color name) and a `threshold` — the number of changed lines at which the *next* category starts:

```yaml
pr-size-thresholds:
  Tiny:
    threshold: 10
    color: lightgray
  Small:
    threshold: 50
    color: green
  Medium:
    threshold: 150
    color: orange
  Large:
    threshold: 300
    color: red
  Massive:
    threshold: inf
    color: darkred
```

This creates the following size buckets:

| Label | Lines Changed |
|---|---|
| `size/Tiny` | 0–9 |
| `size/Small` | 10–49 |
| `size/Medium` | 50–149 |
| `size/Large` | 150–299 |
| `size/Massive` | 300+ |

Each threshold defines the upper boundary of the *previous* category. A PR with 49 lines gets `size/Small` (below the `Medium` threshold of 50), while a PR with 50 lines gets `size/Medium`.

> **Tip:** Use `inf` as the threshold for your largest category to ensure it captures all PRs beyond the previous boundary. Without `inf`, PRs larger than your highest finite threshold still get the last category, but `inf` makes the intent explicit.

You can name categories whatever you want — `Express`, `Standard`, `Premium`, or anything else meaningful to your team. The names become the label suffixes (e.g., `size/Express`).

### How Thresholds Are Sorted

You can define thresholds in any order in YAML. The server always sorts them by threshold value before applying them, so this:

```yaml
pr-size-thresholds:
  Large:
    threshold: 300
    color: red
  Small:
    threshold: 50
    color: green
  Medium:
    threshold: 150
    color: orange
```

produces the same result as listing them in ascending order.

## Advanced Usage

### Repository-Level Overrides

Both `labels` and `pr-size-thresholds` can be set globally or per-repository. Repository-level settings override global ones:

```yaml
# Global defaults
labels:
  enabled-labels:
    - verified
    - hold
    - wip
    - size
    - can-be-merged
  colors:
    hold: red

pr-size-thresholds:
  Small:
    threshold: 50
    color: green
  Large:
    threshold: 300
    color: red

repositories:
  my-repository:
    name: my-org/my-repository
    # Override labels for this repo only
    labels:
      enabled-labels:
        - verified
        - hold
        - size
      colors:
        hold: purple
    # Override size thresholds for this repo only
    pr-size-thresholds:
      Express:
        threshold: 25
        color: lightblue
      Standard:
        threshold: 100
        color: green
      Premium:
        threshold: 500
        color: orange
```

In this example, `my-repository` uses only three label categories with a purple `hold` label and three custom size buckets, while all other repositories use the global settings.

Label colors are deep-merged: repository-level colors override global colors for the same key, but global colors not overridden by the repository still apply.

### In-Repository Configuration

You can also set `labels` and `pr-size-thresholds` in the `.github-webhook-server.yaml` file inside your repository. This file takes the highest precedence — it overrides both global and repository-level `config.yaml` settings. See [Configuring Repositories](configuring-repositories.html) for details on this file.

### Omitting the Color Field

If you omit the `color` field from a size threshold entry, it defaults to `lightgray`:

```yaml
pr-size-thresholds:
  Small:
    threshold: 100
  Large:
    threshold: 500
    color: red
```

Here, `size/Small` labels will be light gray, while `size/Large` labels will be red.

### Using a Single Threshold

You can define as few categories as you like. A single threshold means all PRs get that one size label:

```yaml
pr-size-thresholds:
  Standard:
    threshold: 100
    color: green
```

PRs of any size receive the `size/Standard` label.

## Troubleshooting

**Labels aren't appearing on PRs**
- Verify that `size` (or the relevant category) is listed in `enabled-labels`. If `enabled-labels` is set, only listed categories are active.
- Ensure the webhook server is receiving `pull_request` events. See [Configuring Repositories](configuring-repositories.html) for event configuration.

**Custom size categories aren't working**
- Check that each entry under `pr-size-thresholds` has a valid `threshold` value — it must be a positive integer or `inf`. Zero, negative values, and non-numeric strings are ignored.
- Verify YAML indentation. Each category name should be a key under `pr-size-thresholds`, with `threshold` and `color` nested beneath it.

**Label color looks wrong**
- Colors must be valid CSS3 color names (e.g., `red`, `green`, `darkorange`, `royalblue`). Hex codes and RGB values are not accepted. If an invalid color name is provided, the label defaults to light gray.

**Changes not taking effect**
- The server reloads configuration without requiring a restart. Verify your `config.yaml` changes are saved and the file path is correct. See [Environment Variables](environment-variables.html) for `WEBHOOK_SERVER_DATA_DIR` configuration.

## Related Pages

- [Configuring Repositories](configuring-repositories.html)
- [Configuration Reference](configuration-reference.html)
- [Managing Pull Requests](managing-pull-requests.html)
- [Configuration Recipes](config-recipes.html)
