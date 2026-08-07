import type {
  AtlasConfig,
  AtlasDataSource,
  AtlasEvent,
  AtlasTheme,
} from "../core/types.ts";

export type KnowledgeAtlasProps = {
  dataSource: AtlasDataSource;
  initialFocus?: string;
  config?: AtlasConfig;
  theme?: AtlasTheme;
  /**
   * Label policy (classic-viewer parity): "auto" (default) labels
   * legible, in-core nodes; "on" everything collision allows; "off"
   * focus only. Change takes effect without remounting.
   */
  labelMode?: "auto" | "on" | "off";
  /** Types whose labels are eligible; null/undefined = all types. */
  labelTypes?: readonly string[] | null;
  onEvent?: (event: AtlasEvent) => void;
  onOpenItem?: (id: string) => void;
};
