"""The teacher's demo of a built measure (T-073).

A methodology can ask for a measure the fixed tools do not compute. The build makes a function for
it, checks it, tests it and leaves it waiting for a teacher (registry status `awaiting_demo`). This
package is what the teacher sees and does while it waits:

    compose   pure: what the demo shows, how a "try your own" is checked, what is still missing
              before a confirmation. No database, no sandbox, no clock.
    api       the demo's functions. Each takes the context (the connection and the signed-in
              person) and plain arguments, and writes one action-log row. They open the demo, run
              the function in the sandbox on the teacher's synthetic scores, store a teacher case,
              and record the teacher's decision. Two more are the approver's (T-077): list the
              versions the automated review holds, and clear one hold with a note (it approves
              nothing; the version goes on to the demo and a teacher still decides).

Two rules hold everywhere here:

  * Every number comes from the function, run in the sandbox through `sandbox.with_template`. The
    values it reads come from the school's rulebook and the build's proposed patch, never from a
    model and never from a person's argument.
  * The system never decides. Opening, trying and saving a case leave the version where it was. Only
    the registry's own teacher decision can approve it, by a registered teacher read from the
    session, and the database refuses an approval whose conditions are not met.

Nothing here is a model tool, and nothing here calls a model.
"""
