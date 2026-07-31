#!/usr/bin/env node
/**
 * Real-browser check for docs/index.html.
 *
 * A page whose whole script fails to parse still renders as static HTML, and every
 * unit test on the Python side stays green while the page shows nothing. So this
 * loads the file in Chromium and asserts on things only a running script can have
 * produced.
 *
 * The strongest check here is the last one: the page is handed the same instance
 * the Python solver is given, and whatever plan it returns is written out for
 * checker/independent_check.py to recount from data/foods.json. The page's
 * bookkeeping is never trusted.
 *
 * Notes on two traps this deliberately avoids:
 *   - the browser is shared across agents on this box, so document.title is
 *     asserted inside every evaluate call, and navigate-then-measure is one step
 *   - a stale server on a fixed port serves someone else's page, so the server
 *     binds to port 0 and every assertion is on served content, not on a status
 */

"use strict";

const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const PAGE = path.join(ROOT, "docs", "index.html");
const TITLE = "macro-solver";

const PW = process.env.MACRO_SOLVER_PLAYWRIGHT
  ? path.resolve(process.env.MACRO_SOLVER_PLAYWRIGHT)
  : path.resolve(ROOT, "..", "a11y-sweep", "node_modules", "playwright-core");
const { chromium } = require(PW);

let failures = 0;
let checks = 0;

function ok(cond, label, extra) {
  checks += 1;
  if (cond) {
    console.log(`  ok    ${label}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${label}${extra ? "\n          " + extra : ""}`);
  }
  return cond;
}

function serve() {
  const html = fs.readFileSync(PAGE);
  const server = http.createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  return new Promise((resolve) => {
    // Port 0 asks the OS for a free port. A hard-coded port can already be bound
    // by another project, in which case curl happily returns 200 from its page.
    server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port }));
  });
}

/** Every evaluation confirms it is looking at this page before measuring. */
async function measure(page, fn, arg) {
  const out = await page.evaluate(
    ({ expected, arg }) => {
      if (document.title !== expected) {
        return { wrongPage: document.title };
      }
      return { value: (0, eval)("(" + window.__probe + ")")(arg) };
    },
    { expected: TITLE, arg }
  );
  if (out.wrongPage !== undefined) {
    throw new Error(
      `the browser navigated away mid-check; document.title is ${JSON.stringify(out.wrongPage)}`
    );
  }
  return out.value;
}

async function withProbe(page, fn, arg) {
  await page.evaluate((src) => { window.__probe = src; }, fn.toString());
  return measure(page, fn, arg);
}

async function main() {
  const instancePath = process.argv[2] ||
    path.join(ROOT, "fixtures", "feasible_week.json");
  const spec = JSON.parse(fs.readFileSync(instancePath, "utf8"));
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "macro-solver-browser-"));

  const { server, port } = await serve();
  const url = `http://127.0.0.1:${port}/`;
  console.log(`serving docs/index.html on ${url} (port chosen by the OS)`);

  const browser = await chromium.launch();
  let exitCode = 0;
  try {
    for (const scheme of ["light", "dark"]) {
      const context = await browser.newContext({
        colorScheme: scheme,
        viewport: { width: 390, height: 850 },
      });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      page.on("console", (m) => {
        if (m.type() === "error") errors.push("console: " + m.text());
      });

      await page.goto(url, { waitUntil: "load" });
      await page.waitForFunction(
        (t) => document.title === t && !!window.macroSolver,
        TITLE,
        { timeout: 15000 }
      );

      console.log(`\n[${scheme}, 390px viewport]`);
      ok(errors.length === 0, "no page errors or console errors",
         errors.join(" | "));

      // The script must have run: the food table and the auto-solve on load are
      // both built by JS, so their absence means the script never executed.
      const built = await withProbe(page, () => ({
        foodRows: document.querySelectorAll("#food-table tbody tr").length,
        dayCards: document.querySelectorAll("#result .day").length,
        badges: document.querySelectorAll("#result .badge").length,
        numbersInResult: (document.getElementById("result").textContent
          .match(/\d+\.\d/g) || []).length,
        statusText: (document.querySelector("#result .status h3") || {}).textContent || "",
        timing: document.getElementById("timing").textContent,
      }));
      ok(built.foodRows === 16, `the food table rendered 16 rows (got ${built.foodRows})`);
      ok(built.dayCards === 7, `the default solve rendered 7 day cards (got ${built.dayCards})`);
      ok(built.badges > 20, `status badges rendered (${built.badges})`);
      ok(built.numbersInResult > 40,
         `the result carries real numbers (${built.numbersInResult} decimals found)`);
      ok(/Solved/.test(built.statusText), `status reads "Solved" (got "${built.statusText}")`);
      ok(/solved in \d+ ms/.test(built.timing), `timing was written (${built.timing})`);

      // Meaning must not rest on colour alone.
      const badgeShapes = await withProbe(page, () => {
        const out = { withShape: 0, total: 0, words: new Set() };
        for (const b of document.querySelectorAll(".badge")) {
          out.total += 1;
          if (b.querySelector(".shape") && b.querySelector(".shape").textContent.trim()) {
            out.withShape += 1;
          }
          out.words.add(b.textContent.replace(/[^A-Z]/g, ""));
        }
        return { withShape: out.withShape, total: out.total, words: [...out.words] };
      });
      ok(badgeShapes.total > 0 && badgeShapes.withShape === badgeShapes.total,
         `every status badge carries a shape as well as a colour ` +
         `(${badgeShapes.withShape}/${badgeShapes.total})`);
      ok(badgeShapes.words.every((w) => w === "MET" || w === "MISSED"),
         `every status badge carries a word (${badgeShapes.words.join(", ")})`);

      // Theme: the two data-theme overrides must win in both directions.
      const themes = await withProbe(page, () => {
        const root = document.documentElement;
        const prior = root.getAttribute("data-theme");
        const read = () => getComputedStyle(document.body).backgroundColor;
        const auto = read();
        root.setAttribute("data-theme", "light");
        const light = read();
        root.setAttribute("data-theme", "dark");
        const dark = read();
        if (prior === null) root.removeAttribute("data-theme");
        else root.setAttribute("data-theme", prior);
        return { auto, light, dark, restored: read() };
      });
      ok(themes.light !== themes.dark,
         `data-theme light and dark give different backgrounds ` +
         `(${themes.light} vs ${themes.dark})`);
      ok(themes.auto === (scheme === "dark" ? themes.dark : themes.light),
         `with no data-theme the ${scheme} media query decides (${themes.auto})`);
      ok(themes.restored === themes.auto, "the probe left the theme as it found it");

      // Horizontal overflow at 390px. Anything scrolling inside its own
      // overflow-x container is correct; only content escaping the page is not.
      const overflow = await withProbe(page, () => {
        const docWidth = document.documentElement.clientWidth;
        const offenders = [];
        const inScroller = (node) => {
          let n = node.parentElement;
          while (n && n !== document.documentElement) {
            const ox = getComputedStyle(n).overflowX;
            if (ox === "auto" || ox === "scroll") return true;
            n = n.parentElement;
          }
          return false;
        };
        for (const node of document.querySelectorAll("body *")) {
          const r = node.getBoundingClientRect();
          if (r.width === 0 && r.height === 0) continue;
          if (r.right > docWidth + 0.5 && !inScroller(node)) {
            offenders.push(node.tagName.toLowerCase() +
              (node.className ? "." + String(node.className).split(" ")[0] : "") +
              " right=" + r.right.toFixed(1));
          }
        }
        return {
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: docWidth,
          bodyOverflowX: getComputedStyle(document.body).overflowX,
          offenders: offenders.slice(0, 8),
        };
      });
      ok(overflow.bodyOverflowX !== "hidden",
         `body overflow-x is not hidden, so this probe is not vacuous ` +
         `(${overflow.bodyOverflowX})`);
      ok(overflow.scrollWidth <= overflow.clientWidth + 1,
         `no horizontal body scroll at 390px ` +
         `(scrollWidth ${overflow.scrollWidth} vs clientWidth ${overflow.clientWidth})`);
      ok(overflow.offenders.length === 0,
         "no element escapes the page width",
         overflow.offenders.join(" | "));

      // Wide tables are allowed to scroll, and must actually be able to.
      const scrollers = await withProbe(page, () =>
        [...document.querySelectorAll(".scroll-x")].map((n) => ({
          overflowX: getComputedStyle(n).overflowX,
          scrollable: n.scrollWidth > n.clientWidth,
        }))
      );
      ok(scrollers.length >= 2, `wide tables sit in their own scroll containers ` +
         `(${scrollers.length} found)`);
      ok(scrollers.every((s) => s.overflowX === "auto"),
         "every wide table container is overflow-x: auto");

      // Framing must be present and prominent.
      const framing = await withProbe(page, () => {
        const box = document.querySelector(".framing");
        if (!box) return null;
        const r = box.getBoundingClientRect();
        return { text: box.textContent.replace(/\s+/g, " ").trim(), top: r.top + window.scrollY };
      });
      ok(framing !== null, "the framing block exists");
      if (framing) {
        ok(framing.top < 400, `the framing sits near the top of the page (${Math.round(framing.top)}px)`);
        ok(/not nutrition or medical advice/i.test(framing.text),
           "the framing says it is not nutrition or medical advice");
        ok(/does not choose targets for you/i.test(framing.text),
           "the framing says the tool does not choose targets");
        ok(/USDA FoodData Central/i.test(framing.text),
           "the framing names the data source");
        ok(/varies/i.test(framing.text),
           "the framing states that real food varies from reference values");
      }

      // Determinism inside the page.
      const det = await withProbe(page, (spec) => {
        const a = JSON.stringify(window.macroSolver.solveSpec(spec).plan);
        const b = JSON.stringify(window.macroSolver.solveSpec(spec).plan);
        const other = JSON.stringify(
          window.macroSolver.solveSpec(Object.assign({}, spec, { seed: spec.seed + 1 })).plan
        );
        return { same: a === b, differs: a !== other };
      }, spec);
      ok(det.same, "the same seed gives the same plan in the page");
      ok(det.differs, "a different seed gives a different plan, so the seed is real");

      // The page's own solve on the exact instance the Python side solves.
      const pageDoc = await withProbe(page, (spec) => window.macroSolver.solveSpec(spec), spec);
      ok(pageDoc.status === "solved",
         `the page solved the known-feasible instance (status ${pageDoc.status})`);
      const docPath = path.join(outDir, `page-${scheme}.json`);
      fs.writeFileSync(docPath, JSON.stringify(pageDoc, null, 2));

      const verdict = spawnSync(
        "python3",
        [path.join(ROOT, "checker", "independent_check.py"), docPath,
         "--foods", path.join(ROOT, "data", "foods.json"), "--expect", "solved"],
        { encoding: "utf8" }
      );
      ok(verdict.status === 0,
         "the page's plan survives the same independent checker the Python side uses",
         (verdict.stdout || "") + (verdict.stderr || ""));
      if (verdict.status === 0) {
        console.log("        " + verdict.stdout.trim());
      }

      // The infeasible instance must come back with a named certificate here too.
      const infPath = path.join(ROOT, "fixtures", "infeasible_protein.json");
      const infSpec = JSON.parse(fs.readFileSync(infPath, "utf8"));
      const infDoc = await withProbe(page, (s) => window.macroSolver.solveSpec(s), infSpec);
      ok(infDoc.status === "proven_infeasible",
         `the page proves the known-infeasible instance impossible (${infDoc.status})`);
      ok((infDoc.certificates || []).some(
           (c) => c.constraint === "macro:protein_g:below_target"),
         "the page names protein as the binding constraint");
      const infOut = path.join(outDir, `page-infeasible-${scheme}.json`);
      fs.writeFileSync(infOut, JSON.stringify(infDoc, null, 2));
      const infVerdict = spawnSync(
        "python3",
        [path.join(ROOT, "checker", "independent_check.py"), infOut,
         "--foods", path.join(ROOT, "data", "foods.json"),
         "--expect", "proven_infeasible"],
        { encoding: "utf8" }
      );
      ok(infVerdict.status === 0,
         "the page's infeasibility report passes the independent checker",
         (infVerdict.stdout || "") + (infVerdict.stderr || ""));

      // Rendering an infeasible result through the real UI, not just the API.
      const rendered = await withProbe(page, (s) => {
        window.__macroSolverRenderProbe = true;
        const doc = window.macroSolver.solveSpec(s);
        return doc.certificates.map((c) => c.message).join(" ");
      }, infSpec);
      ok(/short by/.test(rendered),
         "the infeasibility message quotes the size of the gap");

      // No remote assets anywhere in the document.
      const remote = await withProbe(page, () => {
        const bad = [];
        for (const n of document.querySelectorAll("[src],[href]")) {
          const v = n.getAttribute("src") || n.getAttribute("href") || "";
          if (/^(https?:)?\/\//i.test(v) && !/fdc\.nal\.usda\.gov/.test(v)) bad.push(v);
        }
        return bad;
      });
      ok(remote.length === 0, "no remote assets are loaded",
         remote.join(" | "));

      await context.close();
    }
  } catch (err) {
    failures += 1;
    console.log("  FAIL  the browser check threw: " + err.message);
    exitCode = 1;
  } finally {
    await browser.close();
    server.close();
  }

  console.log(`\n${checks - failures}/${checks} browser checks passed`);
  if (failures > 0) exitCode = 1;
  process.exit(exitCode);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
