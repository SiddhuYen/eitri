---
name: combined
description: Design a whole machine in eitri's combined workspace (the combined_* tools, on this computer) - structure, mechanism, wiring, firmware, air and its circuit in one project, built and checked together until every single-domain and cross-domain check passes. Use for robots, arms, rovers, plotters, grippers with their drive and power, and anything spanning two or more domains.
---

# eitri: the combined workspace

The tools come from the `eitri-combined` MCP server, which runs on this computer: `combined_reference`,
`combined_new_project`, `combined_list_projects`, `combined_read_files`, `combined_put_files`, `combined_build`,
`combined_results` and `combined_export`. Your host may show them with a prefix (for example
`mcp__plugin_eitri_eitri-combined__combined_build`). eitri builds every file here and runs all the checks, single-domain and
cross-domain. A project is a folder in ~/eitri-projects; the user watches it in eitri's viewer (a tab per model file). You
write every file yourself.

If these tools are missing, follow the **design** skill (eitri isn't installed yet). There is no hosted or online
version to sign in to: everything runs here.

## The loop

1. **Reference.** Call `combined_reference()` first, then `combined_reference(topic)` for each domain or link you are
   about to write. It gives the project layout, the per-domain files and how the files refer to each other. Write files
   as it says. Don't guess the layout from the local single-domain skills.
2. **Plan.** In 3 to 8 lines, state the machine: its domains, the key numbers (payload, reach, supply voltage, pressures,
   the board) and your assumptions. Ask the user only for a fact that would change the design and that you can't
   reasonably assume.
3. **Project.** `combined_new_project(name, goal)`, where `goal` is the request in a sentence. Before writing any file,
   send the user one short message: the workspace you picked and why, the plan from step 2, and the viewer link the
   project returns. To continue earlier work, use `combined_list_projects()` and `combined_read_files(project_id)` instead.
4. **Write.** `combined_put_files(project_id, {path: content, ...})` sends whole files (with `delete=[path]` to remove
   one). You may also edit the files in the project folder with your own file tools; `combined_build` builds what is on
   disk. Keep every number the user may change in each file's `PARAMS`. Put the request's own requirements in `verify()`.
5. **Build.** `combined_build(project_id)` builds every domain and runs every check. A long build goes on in the
   background: the result then says so, and `combined_results(project_id)` waits for it. Don't start another build.
6. **Read the result.** It lists each check with pass, warn, not run or fail, the measured number, the limit and the
   limit's source. It also lists the **next steps**: what is unbuilt ("wiring not built yet") and why something fails
   ("coupling X fails because..."). Treat the next steps as your to-do list: work through them in order, and add any
   missing domain they name. "Not run" is not a pass: give the missing input.
7. **Fix and rebuild.** Change the design, not the checks: a bigger servo, a thicker wire, a lower gear ratio, a
   regulator with headroom, a longer link. Never delete, loosen or rename a check to make it pass, and never lower a
   requirement the user gave. If you think a check measures the wrong thing, keep it, add the right one, and say so.
   Send only the files you changed, then `combined_build` again.
8. **Done** means every check passes (warnings explained) and the next steps list is empty. Then reply briefly: what you
   built, the numbers that matter (the tightest margins), what the user can change, your assumptions, and the viewer
   link. When the user wants files, use `combined_export(project_id, formats)`, which writes them into the project's
   export/ folder and returns their paths (the build package only when every check has run and passes).

## Notes

- A firmware file's `compiles (arduino-cli)` check fails with "compiler unavailable" until arduino-cli is installed: ask the
  user to run `eitri setup firmware` once in a terminal (it installs system software, so it's their call), then rebuild.
- Results give each failing check's numbers and source. Quote them to the user when you explain a change.
- Work in visible stages on a big machine: get the structure and motion right first, then wiring and power, then
  firmware, then the circuit. Build after each stage and say in one line what you are adding.
- The checks are only as good as the models and figures behind them: say which numbers are estimates or unverified
  catalog figures when they decide a margin.
