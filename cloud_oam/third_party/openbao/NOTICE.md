# OpenBao Agent reuse notice

This directory records the source and license of the OpenBao component selected
for the RSC pilot. OpenBao is an external executable, not a new Python dependency.
No upstream Go implementation is copied into the application.

- Upstream: https://github.com/openbao/openbao
- Version: `v2.7.1`
- Fixed commit: `a5db72cef75c24b920ade02065b18dd8eb666bac`
- License: Mozilla Public License 2.0; the complete upstream `LICENSE` is retained
  here without modification. Preserve upstream notices when redistributing the
  executable or adapting additional source files.
- Original implementation notices: `Copyright (c) HashiCorp, Inc.` and
  `SPDX-License-Identifier: MPL-2.0`, as recorded in the reviewed source files.
- Binary archive/signature/digest evidence remains in
  `deployment/openbao-pilot/official_release_2.7.1.json`.
- Exact source paths and SHA-256 hashes are in `source.json`. The version is
  deliberately pinned; this is not an instruction to execute a moving `main`.

The `deployment/openbao-pilot/agent-autoauth*` candidate configuration and its
synthetic proof adapt the upstream AppRole auto-auth and file-sink documented
interfaces. Local modifications restrict access to a Unix socket, omit the agent
proxy/listener/template/cache features, use a short-lived batch token without the
default policy, and project only the two existing Transit decrypt permissions.
The file sink uses decimal mode `288` (`0440`). The proof driver is project code;
it calls the unmodified official Agent binary and the existing isolated harness.

The adapted configuration is provided under MPL-2.0. It contains no credentials.
Its inclusion does not enable a production service or establish production
identity, credential custody, recovery or release readiness. The upstream license
does not by itself establish that a particular deployment is secure.
