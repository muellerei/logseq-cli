# Shared code is split by what it decides

Code that more than one command uses lives in modules named for the question
each one answers: `dates`, `headings`, `outlinetext`, `ids`, `cliinput`,
`blockprops`, `strictinsert` and `lookup`, next to `blocktext`, `pagenames`,
`datalog` and `render`. They replaced `helpers.py`, 2165 lines that held date
parsing, the outline parser, the `id::` contract, Strict Insert, property rules
and graph lookups side by side. The modules sit flat in `logseq_cli/`, and a
caller imports a name from the module that defines it.

## Considered Options

**Split by test level: what needs `LogseqAPI` and what does not.** This was the
first design. Rejected: the functions that need no API were already testable
without a mock, and no defect ever came from one of them gaining an `api`
parameter, so the split would have protected nothing. It also groups badly.
Measured on 2026-09-24 over the 37 commits that changed `helpers.py` and touched
a name still there, splitting by what a function decides keeps 23 of them within
a single module, 1.62 modules per commit on average. Splitting by test level
keeps 19, at 1.81. The two layouts have 7 and 30 call edges between modules.

**Moving each helper into the one command module that uses it.** Measured on the
same date: 12 of the 73 public names had exactly one caller, a command module,
and no use inside `helpers.py`. The other 61 were used by several modules or by
`helpers.py` itself. Moving the 12 would have left a smaller `helpers.py` with
the same name and the same mix.

**Taking out only the largest parts**, such as `strictinsert` and `ids`. That
covers most of the commits, but the rest still sits under a name that says
nothing about it, and that name was the problem.

**A package `logseq_cli/helpers/` with the same modules inside it.** Rejected:
it keeps the collective name. `tests/test_package_layering.py` reads
`logseq_cli/*.py`, so a flat module falls under its rules from the day it is
added and a subpackage does not. `pyproject.toml` also lists packages one by one.

**Re-exporting every name from `helpers`**, so that no import has to change.
Rejected: it gives each name two addresses, and it would not have spared the
tests anyway. A trial run with the re-export in place still failed two tests
(2026-09-24).

## Consequences

`logseq_cli.helpers` no longer exists. A new shared function goes into the
module whose question it answers. If none fits, it gets a new module named for
its own question, not another collection.

The split was not made by test level, but three of the modules turned out to
need no API: `dates`, `outlinetext` and `cliinput`. Their docstrings say so.
`tests/test_package_layering.py` keeps it true: none of them may take `api` or
import a module that does. The same file also checks that the import graph of the
package has no cycle, counting imports inside functions. Python accepts some
cycles without complaint. A plain `import` of `pagenames` placed at the end of
`datalog.py` passed the whole suite in a trial (2026-09-24).

`git blame` shows the moving commit on every line of the new modules;
`git blame -C` follows the lines to where they were written.
