# investment-research plugin

Research campaigns over a universe of listed products (equities, ETFs), under measured guarantees: jobs
take a hashed snapshot of daily bars, profile the universe and backtest the strategies an agent proposes
in a closed grammar; one memo per relevant product quotes only the numbers the backtest measured, and a
person decides on each product and on the campaign. A research note for internal use, never an order.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `investment-research` 0.1.1, core `>=0.27.14,<1.0.0` |
| Template | [`templates/investment-research`](templates/investment-research/README.md) | 0.2.0: the `campaign` and `product-memo` workflows, the agents `quant-strategist` and `quant-analyst`, the skills `strategy-grammar` and `reading-metrics`, its eval cases |
| Procedures | [`procedures/`](procedures) | the `marketdata` connector types: `stooq`, `yahoo`, `alphavantage`, `tiingo`, `composite` |

## The two halves

ADR 0181 of the core ("Decision (2026-11-19)", points 2 and 4; core story S83-07) moves the investment
research trade out of the core into a unit of its own. It has two halves:

- **The content, here.** The template and the connector procedures, published as the plugin
  `investment-research` from the source `tranibor`. The template was copied from the core unchanged,
  except that its three job steps name their image (`image: investment-research`) and its version is
  0.2.0. Its agents and skills stay template-local (`defaults.agents`, `defaults.skills`): the core
  resolves them against the template's folder.
- **The code, in the core repository.** The extension `tranibor-investment-research`, which ships the
  `marketdata` connector family and is installed in the platform's published API and worker images; and
  the `investment-research` job image, `ghcr.io/graalsystems/tranibor-investment-research:<core version>`,
  which the core's release builds FROM that release's runner image and which carries `choregos-quant`.

The plugin format has no field that names an extension or a job image, so `plugin.yaml` declares neither;
it declares the organisation connector the template calls (`requires.org_connectors: {marketdata:
marketdata}`), which the installer does not enforce.

## What the operator does

1. **Install the plugin** from the `tranibor` source (Catalogue, **Plugins**, or the deployment's
   `defaultPlugins`, see the [root README](../../README.md#installing)). Projects are created from the
   template `plugin:investment-research/investment-research`.
2. **Map the job image.** `CHOREGOS_JOB_IMAGES` (or the chart's
   `global.images.jobs.investment-research`) maps `investment-research` to
   `ghcr.io/graalsystems/tranibor-investment-research:<core version>`. On a core older than the one whose
   release builds that image, map it to that core's runner image, which still carries `choregos-quant`.
3. **Nothing to install for the extension.** It is in the platform's published API and worker images.
   The organisation then creates its `marketdata` connector (Connectors, **Market data**) following one of
   the procedures.

## Checks

```sh
tranibor plugin validate plugins/investment-research
tranibor plugin pack plugins/investment-research -o investment-research-0.1.1.tar.gz   # same bytes every time
```
