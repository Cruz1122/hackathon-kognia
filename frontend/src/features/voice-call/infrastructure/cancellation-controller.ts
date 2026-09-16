export class CancellationController {
  private generation = 0;
  private activeAbort?: AbortController;

  begin(): { id: number; signal: AbortSignal } {
    this.cancel('superseded');
    this.activeAbort = new AbortController();
    return { id: ++this.generation, signal: this.activeAbort.signal };
  }

  cancel(_reason = 'cancelled'): void {
    this.activeAbort?.abort();
    this.activeAbort = undefined;
    this.generation += 1;
  }

  isCurrent(id: number): boolean {
    return id === this.generation && !this.activeAbort?.signal.aborted;
  }
}
