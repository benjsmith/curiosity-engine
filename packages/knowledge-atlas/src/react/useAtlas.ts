/**
 * useAtlas — the engine without the prefab component, for hosts that
 * bring their own canvas/render loop.
 */

import { useEffect, useMemo, useState } from "react";
import { AtlasEngine } from "../core/engine.ts";
import type { AtlasConfig, AtlasDataSource, AtlasState } from "../core/types.ts";

export function useAtlas(
  dataSource: AtlasDataSource,
  config?: AtlasConfig,
): { engine: AtlasEngine; state: AtlasState } {
  const engine = useMemo(() => new AtlasEngine(dataSource, config), [dataSource, config]);
  const [state, setState] = useState<AtlasState>(() => engine.getState());
  useEffect(() => {
    const off = engine.on(() => setState(engine.getState()));
    return () => {
      off();
      engine.destroy();
    };
  }, [engine]);
  return { engine, state };
}
