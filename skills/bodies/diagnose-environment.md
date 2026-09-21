# diagnose-environment

The failure is environmental until proven otherwise.

## Sequence

1. Check the command exists on PATH and its version.
2. Check the working directory and the files it expects.
3. Check environment variables, services, and credentials.
4. Check permissions on the failing paths.
5. Only if everything checks out, re-escalate to code investigation.

## Never

- Edit product code to work around a missing tool, service, or permission.
- Invent credentials or install unrequested dependencies.
