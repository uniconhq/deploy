import { readFileSync } from "node:fs";
import { expect } from "chai";

function sum(numbers: number[]): number {
  return numbers.reduce((total, each) => total + each, 0);
}

expect(sum([1, 2, 3, 4])).to.equal(10);
console.log(sum(readFileSync(0, "utf8").trim().split(/\s+/).map(Number)));
