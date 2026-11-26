declare module "d3-force" {
  export interface ForceCollide<NodeDatum> {
    (alpha: number): void;
    radius(radius: number | ((node: NodeDatum, i: number, nodes: NodeDatum[]) => number)): this;
    strength(strength: number): this;
    iterations(iterations: number): this;
  }

  export function forceCollide<NodeDatum = unknown>(
    radius?: number | ((node: NodeDatum, i: number, nodes: NodeDatum[]) => number)
  ): ForceCollide<NodeDatum>;
}
