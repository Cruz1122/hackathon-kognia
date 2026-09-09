const sentenceBoundary = /[.!?](?:["'»”)]*)?(?=\s|$)/;
const clauseBoundary = /[;:](?:\s|$)/;

export class SemanticChunker {
  private buffer = '';
  private readonly minWords: number;
  private readonly maxWords: number;

  constructor(
    minWords = 8,
    maxWords = 20,
  ) {
    this.minWords = minWords;
    this.maxWords = maxWords;
  }

  push(text: string): string[] {
    this.buffer += text;
    const chunks: string[] = [];
    while (true) {
      const cut = this.findCut();
      if (cut === -1) break;
      chunks.push(this.take(cut));
    }
    return chunks;
  }

  flush(): string[] {
    const value = this.buffer.trim();
    this.buffer = '';
    return value ? [value] : [];
  }

  reset(): void {
    this.buffer = '';
  }

  private findCut(): number {
    const words = this.buffer.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) return -1;
    const sentence = this.buffer.search(sentenceBoundary);
    if (sentence >= 0 && this.wordCount(this.buffer.slice(0, sentence + 1)) >= 3) {
      return sentence + 1;
    }
    const clause = this.buffer.search(clauseBoundary);
    if (clause >= 0 && this.wordCount(this.buffer.slice(0, clause + 1)) >= this.minWords) {
      return clause + 1;
    }
    if (words.length >= this.maxWords) {
      const wordEnds = [...this.buffer.matchAll(/\S+/g)];
      return wordEnds[this.maxWords - 1]?.index !== undefined
        ? wordEnds[this.maxWords - 1].index + wordEnds[this.maxWords - 1][0].length
        : -1;
    }
    return -1;
  }

  private take(end: number): string {
    const chunk = this.buffer.slice(0, end).trim();
    this.buffer = this.buffer.slice(end).trimStart();
    return chunk;
  }

  private wordCount(text: string): number {
    return text.trim().split(/\s+/).filter(Boolean).length;
  }
}
