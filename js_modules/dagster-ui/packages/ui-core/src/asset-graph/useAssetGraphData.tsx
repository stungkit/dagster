import keyBy from 'lodash/keyBy';
import reject from 'lodash/reject';
import {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {FeatureFlag} from 'shared/app/FeatureFlags.oss';
import {useAssetGraphSupplementaryData} from 'shared/asset-graph/useAssetGraphSupplementaryData.oss';
import {Worker} from 'shared/workers/Worker.oss';

import {ASSET_NODE_FRAGMENT} from './AssetNode';
import {GraphData, buildGraphData as buildGraphDataImpl, tokenForAssetKey} from './Utils';
import {gql} from '../apollo-client';
import {computeGraphData as computeGraphDataImpl} from './ComputeGraphData';
import {BuildGraphDataMessageType, ComputeGraphDataMessageType} from './ComputeGraphData.types';
import {AssetGraphQueryItem, AssetNode} from './types';
import {featureEnabled} from '../app/Flags';
import {
  AssetGraphQuery,
  AssetGraphQueryVariables,
  AssetGraphQueryVersion,
  AssetNodeForGraphQueryFragment,
} from './types/useAssetGraphData.types';
import {GraphQueryItem} from '../app/GraphQueryImpl';
import {indexedDBAsyncMemoize} from '../app/Util';
import {usePrefixedCacheKey} from '../app/usePrefixedCacheKey';
import {AssetKey} from '../assets/types';
import {AssetGroupSelector, PipelineSelector} from '../graphql/types';
import {useBlockTraceUntilTrue} from '../performance/TraceContext';
import {useIndexedDBCachedQuery} from '../search/useIndexedDBCachedQuery';
import {workerSpawner} from '../workers/workerSpawner';

export interface AssetGraphFetchScope {
  hideEdgesToNodesOutsideQuery?: boolean;
  hideNodesMatching?: (node: AssetNodeForGraphQueryFragment) => boolean;
  pipelineSelector?: Pick<
    PipelineSelector,
    'pipelineName' | 'repositoryName' | 'repositoryLocationName'
  >;
  groupSelector?: Pick<
    AssetGroupSelector,
    'groupName' | 'repositoryName' | 'repositoryLocationName'
  >;
  kinds?: string[];

  externalAssets?: {id: string; key: {path: Array<string>}}[];

  // This is used to indicate we shouldn't start handling any input.
  // This is used by pages where `hideNodesMatching` is only available asynchronously.
  loading?: boolean;
  useWorker?: boolean;
}

export function useFullAssetGraphData(
  options: Omit<AssetGraphFetchScope, 'groupSelector' | 'pipelineSelector'>,
) {
  const fetchResult = useIndexedDBCachedQuery<AssetGraphQuery, AssetGraphQueryVariables>({
    query: ASSET_GRAPH_QUERY,
    key: usePrefixedCacheKey('AssetGraphQuery'),
    version: AssetGraphQueryVersion,
  });

  const spawnBuildGraphDataWorker = useMemo(
    () => workerSpawner(() => new Worker(new URL('./ComputeGraphData.worker', import.meta.url))),
    [],
  );
  useEffect(() => {
    return () => {
      spawnBuildGraphDataWorker.terminate();
    };
  }, [spawnBuildGraphDataWorker]);

  const externalAssetNodes = useMemo(
    () => (options.externalAssets ?? []).map((a) => buildExternalAssetQueryItem(a)),
    [options.externalAssets],
  );
  const nodes = fetchResult.data?.assetNodes;
  const allNodes = useMemo(
    () => [...(nodes ?? []), ...externalAssetNodes],
    [nodes, externalAssetNodes],
  );

  const [fullAssetGraphData, setFullAssetGraphData] = useState<GraphData | null>(null);
  useBlockTraceUntilTrue('FullAssetGraphData', !!fullAssetGraphData);

  const lastProcessedRequestRef = useRef(0);
  const currentRequestRef = useRef(0);

  useEffect(() => {
    if (options.loading) {
      return;
    }
    const requestId = ++currentRequestRef.current;
    buildGraphData(
      {
        nodes: allNodes,
      },
      spawnBuildGraphDataWorker,
      options.useWorker ?? true,
    )
      ?.then((data) => {
        if (lastProcessedRequestRef.current < requestId) {
          lastProcessedRequestRef.current = requestId;
          setFullAssetGraphData(data);
        }
      })
      .catch((e) => {
        // buildGraphData uses spawnBuildGraphDataWorker, which rejects promises when another call is made before the previous one finishes.
        console.warn(e);
      });
  }, [allNodes, options.loading, options.useWorker, spawnBuildGraphDataWorker]);

  return {fullAssetGraphData, loading: !fetchResult.data || fetchResult.loading || options.loading};
}

export type GraphDataState = {
  graphAssetKeys: AssetKey[];
  allAssetKeys: AssetKey[];
  assetGraphData: GraphData | null;
};
const INITIAL_STATE: GraphDataState = {
  graphAssetKeys: [],
  allAssetKeys: [],
  assetGraphData: null,
};

/** Fetches data for doing asset selection filtering in the asset catalog and asset graph:
 *
 * @param pipelineSelector: Optionally scope to an asset job, or pass null for the global graph
 *
 * @param opsQuery: filter the returned graph using selector syntax string (eg: asset_name++)
 *
 * @param filterNodes: filter the returned graph using the provided function. The global graph
 * uses this option to implement the "3 of 4 repositories" picker.
 */
export function useAssetGraphData(opsQuery: string, options: AssetGraphFetchScope) {
  const fetchResult = useIndexedDBCachedQuery<AssetGraphQuery, AssetGraphQueryVariables>({
    query: ASSET_GRAPH_QUERY,
    key: usePrefixedCacheKey('AssetGraphQuery'),
    version: AssetGraphQueryVersion,
  });

  const nodes = fetchResult.data?.assetNodes;
  const externalAssetNodes = useMemo(
    () => (options.externalAssets ?? []).map(buildExternalAssetQueryItem),
    [options.externalAssets],
  );

  const allNodes = useMemo(
    () => [...(nodes ?? []), ...externalAssetNodes],
    [nodes, externalAssetNodes],
  );

  const {pipelineSelector, groupSelector, hideNodesMatching} = options;

  const repoFilteredNodes = useMemo(() => {
    let matching = allNodes;
    if (pipelineSelector) {
      matching = matching.filter((node) => {
        return (
          node.jobNames.includes(pipelineSelector.pipelineName) &&
          node.repository.name === pipelineSelector.repositoryName &&
          node.repository.location.name === pipelineSelector.repositoryLocationName
        );
      });
    }
    if (groupSelector) {
      matching = matching.filter((node) => {
        return (
          node.groupName === groupSelector.groupName &&
          node.repository.name === groupSelector.repositoryName &&
          node.repository.location.name === groupSelector.repositoryLocationName
        );
      });
    }
    if (hideNodesMatching) {
      matching = reject(matching, hideNodesMatching);
    }
    return matching;
  }, [allNodes, pipelineSelector, groupSelector, hideNodesMatching]);

  const graphQueryItems = useMemo(
    () => buildGraphQueryItems(repoFilteredNodes),
    [repoFilteredNodes],
  );

  const [state, setState] = useState<GraphDataState>(INITIAL_STATE);

  const {kinds, hideEdgesToNodesOutsideQuery} = options;

  const [graphDataLoading, setGraphDataLoading] = useState(true);

  const lastProcessedRequestRef = useRef(0);
  const currentRequestRef = useRef(0);

  const {loading: supplementaryDataLoading, data: supplementaryData} =
    useAssetGraphSupplementaryData(opsQuery, allNodes);

  const spawnComputeGraphDataWorker = useMemo(
    () => workerSpawner(() => new Worker(new URL('./ComputeGraphData.worker', import.meta.url))),
    [],
  );
  useEffect(() => {
    return () => {
      spawnComputeGraphDataWorker.terminate();
    };
  }, [spawnComputeGraphDataWorker]);

  useLayoutEffect(() => {
    if (options.loading || supplementaryDataLoading) {
      return;
    }

    const requestId = ++currentRequestRef.current;

    if (repoFilteredNodes === undefined || graphQueryItems === undefined) {
      lastProcessedRequestRef.current = requestId;
      setState({allAssetKeys: [], graphAssetKeys: [], assetGraphData: null});
      return;
    }

    setGraphDataLoading(true);

    computeGraphData(
      {
        repoFilteredNodes,
        graphQueryItems,
        opsQuery,
        kinds,
        hideEdgesToNodesOutsideQuery,
        supplementaryData,
      },
      spawnComputeGraphDataWorker,
      options.useWorker ?? true,
    )
      ?.then((data) => {
        if (lastProcessedRequestRef.current < requestId) {
          lastProcessedRequestRef.current = requestId;
          setState(data);
          if (requestId === currentRequestRef.current) {
            setGraphDataLoading(false);
          }
        }
      })
      .catch((e) => {
        // computeGraphData uses spawnComputeGraphDataWorker, which rejects promises when another call is made before the previous one finishes.
        console.warn(e);
        if (requestId === currentRequestRef.current) {
          setGraphDataLoading(false);
        }
      });
    return () => {
      // increase the last processed request ref to effectively cancel any outstanding request
      lastProcessedRequestRef.current = requestId;
    };
  }, [
    repoFilteredNodes,
    graphQueryItems,
    opsQuery,
    kinds,
    hideEdgesToNodesOutsideQuery,
    options.loading,
    supplementaryData,
    supplementaryDataLoading,
    spawnComputeGraphDataWorker,
    options.useWorker,
  ]);

  const loading = fetchResult.loading || graphDataLoading || supplementaryDataLoading;
  useBlockTraceUntilTrue('useAssetGraphData', !loading);
  return {
    loading,
    fetchResult,
    assetGraphData: state.assetGraphData,
    graphQueryItems,
    graphAssetKeys: state.graphAssetKeys,
    allAssetKeys: state.allAssetKeys,
  };
}

const computeGraphData = indexedDBAsyncMemoize<GraphDataState, typeof computeGraphDataWrapper>(
  computeGraphDataWrapper,
  (props) => {
    return JSON.stringify(props);
  },
);

const buildGraphQueryItems = (nodes: AssetNode[]) => {
  const items: {[name: string]: AssetGraphQueryItem} = {};

  for (const node of nodes) {
    const name = tokenForAssetKey(node.assetKey);
    items[name] = {
      node,
      name,
      inputs: node.dependencyKeys.map((key) => ({
        dependsOn: [{solid: {name: tokenForAssetKey(key)}}],
      })),
      outputs: node.dependedByKeys.map((key) => ({
        dependedBy: [{solid: {name: tokenForAssetKey(key)}}],
      })),
    };
  }
  return Object.values(items);
};

export const calculateGraphDistances = (items: GraphQueryItem[], assetKey: AssetKey) => {
  const map = keyBy(items, (g) => g.name);
  const start = map[tokenForAssetKey(assetKey)];
  if (!start) {
    return {upstream: 0, downstream: 0};
  }

  let upstreamDepth = -1;
  let candidates = new Set([start.name]);

  while (candidates.size > 0) {
    const nextCandidates: Set<string> = new Set();
    upstreamDepth += 1;

    candidates.forEach((candidate) => {
      map[candidate]!.inputs.flatMap((i) =>
        i.dependsOn.forEach((d) => {
          if (!candidates.has(d.solid.name)) {
            nextCandidates.add(d.solid.name);
          }
        }),
      );
    });
    candidates = nextCandidates;
  }

  let downstreamDepth = -1;
  candidates = new Set([start.name]);

  while (candidates.size > 0) {
    const nextCandidates: Set<string> = new Set();
    downstreamDepth += 1;

    candidates.forEach((candidate) => {
      map[candidate]!.outputs.flatMap((i) =>
        i.dependedBy.forEach((d) => {
          if (!candidates.has(d.solid.name)) {
            nextCandidates.add(d.solid.name);
          }
        }),
      );
    });
    candidates = nextCandidates;
  }

  return {
    upstream: upstreamDepth,
    downstream: downstreamDepth,
  };
};

export const ASSET_GRAPH_QUERY = gql`
  query AssetGraphQuery($pipelineSelector: PipelineSelector, $groupSelector: AssetGroupSelector) {
    assetNodes(pipeline: $pipelineSelector, group: $groupSelector) {
      id
      ...AssetNodeForGraphQuery
    }
  }

  fragment AssetNodeForGraphQuery on AssetNode {
    id
    groupName
    isExecutable
    changedReasons
    tags {
      key
      value
    }
    owners {
      ... on TeamAssetOwner {
        team
      }
      ... on UserAssetOwner {
        email
      }
    }
    tags {
      key
      value
    }
    hasMaterializePermission
    repository {
      id
      name
      location {
        id
        name
      }
    }
    dependencyKeys {
      path
    }
    dependedByKeys {
      path
    }
    ...AssetNodeFragment
  }

  ${ASSET_NODE_FRAGMENT}
`;

const EMPTY_GRAPH_DATA: GraphData = {
  nodes: {},
  downstream: {},
  upstream: {},
};

const EMPTY_GRAPH_DATA_STATE: GraphDataState = {
  graphAssetKeys: [],
  allAssetKeys: [],
  assetGraphData: EMPTY_GRAPH_DATA,
};

let _id = 0;
async function computeGraphDataWrapper(
  props: Omit<ComputeGraphDataMessageType, 'id' | 'type'>,
  spawnComputeGraphDataWorker: () => Worker,
  useWorker: boolean,
): Promise<GraphDataState> {
  if (featureEnabled(FeatureFlag.flagAssetSelectionWorker) && useWorker) {
    const worker = spawnComputeGraphDataWorker();
    return new Promise<GraphDataState>((resolve, reject) => {
      const id = ++_id;
      const removeMessageListener = worker.onMessage((event: MessageEvent) => {
        const data = event.data as GraphDataState & {id: number};
        if (data.id === id) {
          resolve(data);
          removeMessageListener();
          worker.terminate();
        }
      });
      const message: ComputeGraphDataMessageType = {
        type: 'computeGraphData',
        id,
        ...props,
      };
      worker.onError((error) => {
        console.error(error);
        resolve(EMPTY_GRAPH_DATA_STATE);
        worker.terminate();
      });
      worker.onTerminate(() => {
        reject(new Error('Worker terminated'));
      });
      worker.postMessage(message);
    });
  }
  return computeGraphDataImpl(props);
}

const buildGraphData = indexedDBAsyncMemoize<GraphData, typeof buildGraphDataWrapper>(
  buildGraphDataWrapper,
  (props) => {
    return JSON.stringify(props);
  },
);

async function buildGraphDataWrapper(
  props: Omit<BuildGraphDataMessageType, 'id' | 'type'>,
  spawnBuildGraphDataWorker: () => Worker,
  useWorker: boolean,
): Promise<GraphData> {
  if (featureEnabled(FeatureFlag.flagAssetSelectionWorker) && useWorker) {
    const worker = spawnBuildGraphDataWorker();
    return new Promise<GraphData>((resolve) => {
      const id = ++_id;
      const removeMessageListener = worker.onMessage((event: MessageEvent) => {
        const data = event.data as GraphData & {id: number};
        if (data.id === id) {
          resolve(data);
          removeMessageListener();
          worker.terminate();
        }
      });
      worker.onError((error) => {
        console.error(error);
        resolve(EMPTY_GRAPH_DATA);
        worker.terminate();
      });
      const message: BuildGraphDataMessageType = {
        type: 'buildGraphData',
        id,
        ...props,
      };
      worker.postMessage(message);
    });
  }
  return buildGraphDataImpl(props.nodes);
}

const buildExternalAssetQueryItem = (asset: {
  id: string;
  key: {path: string[]};
}): AssetNodeForGraphQueryFragment => {
  return {
    __typename: 'AssetNode',
    changedReasons: [],
    kinds: [],
    hasMaterializePermission: false,
    opVersion: null,
    isMaterializable: false,
    isAutoCreatedStub: true,
    tags: [],
    owners: [],
    id: asset.id,
    groupName: '',
    isExecutable: false,
    isPartitioned: false,
    opNames: [],
    jobNames: [],
    computeKind: null,
    isObservable: false,
    description: null,
    repository: {
      __typename: 'Repository',
      id: '',
      name: '',
      location: {
        __typename: 'RepositoryLocation',
        id: '',
        name: '',
      },
    },
    assetKey: {
      __typename: 'AssetKey',
      ...asset.key,
    },
    graphName: null,
    dependencyKeys: [],
    dependedByKeys: [],
  };
};
