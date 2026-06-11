// plotly.js-basic-dist-min ships no types; it exposes the same API as
// plotly.js (scatter/line traces are all the basic bundle contains).
declare module "plotly.js-basic-dist-min" {
  import * as Plotly from "plotly.js";

  export = Plotly;
}
