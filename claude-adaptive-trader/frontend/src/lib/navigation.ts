import type { NavigateFunction } from "react-router-dom";

// Open an experiment in the Backtesting Laboratory (the page reads its selection from storage).
export function openExperiment(navigate: NavigateFunction, id: string): void {
  try {
    localStorage.setItem("cat.lab.selected", JSON.stringify(id));
  } catch {
    /* storage unavailable: the page opens without a selection */
  }
  navigate("/backtesting");
}
