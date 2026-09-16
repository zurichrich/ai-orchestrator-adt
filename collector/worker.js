// Copyright 2026 zurichrich
// SPDX-License-Identifier: Apache-2.0
//
// ADT-170 3e — Cloudflare Worker entry. Binding-specific glue only; all the
// logic worth testing lives in collector.js and is tested there.
//
// ADT-224 A2a: the request path moved to `handle(request, env)` so the
// put-accounting cases can drive the real code with stub bindings. This file is
// now the entry point and the Durable Object re-export, and nothing else — a
// branch added here would be untested by construction.
import { handle, IpBudget } from "./collector.js";

export { IpBudget };

export default {
  async fetch(request, env) {
    return handle(request, env);
  },
};
