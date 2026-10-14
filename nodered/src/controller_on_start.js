// Fresh state on every deploy/start: the reader process (child of the exec node) does not survive a redeploy.
context.set("S", null);
