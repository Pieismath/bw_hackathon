# Bridgewater Hackathon Project

## First Mentor Chat

#### MVP: Multi-agent approach
- Step 0: Agent to understand the paper
- Step 1: Find the data
- Step 2: Perform the analysis
- Step 3: Find conclusions
#### Risks 
- What degree of human regularization do you apply? 
- How do you set a validation process? 
- How do you ensure it's verifiable?
- Graph structure is secondary to MVP
#### Ideas
- How do you divide and conquer?
  - Think about who's good at what
- How do we have test inputs and outputs at each step?
- Construct a strategy and then test it (Step 0-2)
- Start as simple as possible
- Writing tools for the model to use (Force skills)
#### Output
- How good is the strategy?
  - Is it correlated with the market?
  - How much are we trading relative to returns?
  - How much signal turnover is there?
- How robust is the strategy?
  - If you lag your signal, does it deteriorate?
- Robert Tips: Imagine being CIO, need to know if alpha is tradeable or not
  - Tradeability Scorecard
    - Turnover & Capacity: Calculate rebalancing frequency, if too high probably no alpha
    - Use LLM for heuristics
  - Robustness & Decay Stress Test
    - If trade 2 days late is alpha still there or is signal too fragile
    - Does it work across regimes like 2008 and 2022
  - Show gaps if paper has gaps

## Second Mentor Chat

Good call on only picking trading strategy paper

### Data
- Should work with various datasets
- Converting paper signal into strategy may not be super possible
- Think about "we don't have enough data" vs "the paper is flawed"
- Contextualize with news

### Contextualize with news
- Think about context files, provide the data
- Validation is heavy here, they may create plausible but false stories
- Something that can be added on at the end if possible

### Backtest
- Tunable parameters (Change between monthly/daily returns, transaction size)
- Slice into certain time periods
- Interactivity

### Signal
- Correlation with returns
- t-statistics

### Agent Pipeline
- Building toward sample paper (try out of sample)
- Where do the agents struggle?

## Planned Features (Phase 3+)

- **Tradeability Scorecard** — turnover, capacity, rebalance frequency, gross/net spread. LLM heuristic on top that gives a CIO-style "tradeable / borderline / not tradeable" verdict with reasons.
- **Robustness / decay stress test** — re-run the backtest with signal lagged 1/2/5/10 days, slice by regime (pre-2008, 2008 crisis, 2010s, 2020-2022). Render as a small heatmap.
- **Paper-vs-replication diff** — extract the paper's reported Sharpe/returns in A1, then show "paper claims X, we got Y" side-by-side. The single most convincing artifact for a judge.
- **News contextualization (guarded)** — optional tab that pulls headlines around drawdown periods; requires a verifier like A2 so the model can't fabricate stories.