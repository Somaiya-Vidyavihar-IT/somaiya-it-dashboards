# Viz.js (Graphviz in WebAssembly)

`viz-global.js` is the unmodified `dist/viz-global.js` from the npm package
[`@viz-js/viz` 3.31.0](https://www.npmjs.com/package/@viz-js/viz/v/3.31.0)
(tarball integrity `sha512-r7zlQdRvcwvIpjHGgs+KNWHeE/P5/Dq7k8ZAKaMCbZqomxCBJV78gVuQYaHzFVca+kB0mX0fMW9UFevCOBG50A==`).

It is used only by the Network Monitoring **Topology → Diagram** view, which lays out the
collector's Graphviz DOT file in the browser. It is loaded on first use, not with the page,
and makes no network requests (the WebAssembly is embedded).

Licences: Viz.js is MIT (Copyright (c) Michael Daines). The file contains Graphviz
(Eclipse Public License 1.0, https://graphviz.org/license/) and Expat (MIT) in object-code form;
their source is available from https://gitlab.com/graphviz/graphviz and https://github.com/libexpat/libexpat.

To update: download the new tarball from npm, check its `integrity` against the registry,
copy `dist/viz-global.js` here, and update the version above.
