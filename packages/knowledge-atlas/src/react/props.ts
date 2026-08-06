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
  onEvent?: (event: AtlasEvent) => void;
  onOpenItem?: (id: string) => void;
};
