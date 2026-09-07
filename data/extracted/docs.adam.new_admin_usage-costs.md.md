<!-- source: https://docs.adam.new/admin/usage-costs.md fetched 2026-09-07T18:00:33+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Usage costs by tier

> Compare how Lite, Pro, and Ultra use your Adam usage allowance.

export const UsageCostComparison = () => {
  const INPUT_LIMITS = {
    min: 1000,
    max: 300000,
    step: 1000
  };
  const OUTPUT_LIMITS = {
    min: 500,
    max: 100000,
    step: 500
  };
  const formatTokens = value => new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 0
  }).format(value);
  const formatCompactTokens = value => new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 0
  }).format(value);
  const formatMultiplier = value => new Intl.NumberFormat("en-US", {
    minimumFractionDigits: value < 1 ? 2 : 0,
    maximumFractionDigits: value < 1 ? 2 : 1
  }).format(value);
  const isRateSet = value => value !== null && typeof value === "object" && Number.isFinite(value.input) && value.input > 0 && Number.isFinite(value.output) && value.output > 0;
  const normalizeResponse = value => {
    if (value === null || typeof value !== "object" || !Array.isArray(value.tiers) || value.tiers.length === 0) {
      throw new Error("The usage-tier response is not valid.");
    }
    const normalizedTiers = value.tiers.map((tier, index) => {
      if (tier === null || typeof tier !== "object" || typeof tier.label !== "string" || tier.label.trim().length === 0 || typeof tier.description !== "string" || typeof tier.isBaseline !== "boolean" || !isRateSet(tier.relativeRates)) {
        throw new Error("A usage tier is missing required comparison data.");
      }
      let longContext = null;
      if (tier.longContext !== null && tier.longContext !== undefined) {
        if (typeof tier.longContext !== "object" || !Number.isFinite(tier.longContext.atInputTokens) || tier.longContext.atInputTokens <= 0 || !isRateSet(tier.longContext.relativeRates)) {
          throw new Error("A usage tier contains invalid comparison data.");
        }
        longContext = {
          atInputTokens: tier.longContext.atInputTokens,
          relativeRates: tier.longContext.relativeRates
        };
      }
      let fastMode = null;
      if (tier.fastMode !== null && tier.fastMode !== undefined) {
        if (typeof tier.fastMode !== "object" || !Number.isFinite(tier.fastMode.usageMultiplier) || tier.fastMode.usageMultiplier <= 0) {
          throw new Error("A usage tier contains invalid Fast mode data.");
        }
        fastMode = {
          usageMultiplier: tier.fastMode.usageMultiplier
        };
      }
      return {
        description: tier.description,
        fastMode,
        id: `${tier.label.trim().toLowerCase()}-${index}`,
        isBaseline: tier.isBaseline,
        label: tier.label.trim(),
        longContext,
        relativeRates: tier.relativeRates
      };
    });
    if (normalizedTiers.filter(tier => tier.isBaseline).length !== 1) {
      throw new Error("The usage-tier response has no clear baseline.");
    }
    return normalizedTiers;
  };
  const activeRatesFor = (tier, inputTokens) => tier.longContext !== null && inputTokens >= tier.longContext.atInputTokens ? tier.longContext.relativeRates : tier.relativeRates;
  const calculateCost = (rates, inputTokens, outputTokens) => inputTokens * rates.input + outputTokens * rates.output;
  const estimateTiers = (tiers, baselineTier, inputTokens, outputTokens, fastOnly) => {
    const costs = tiers.map(tier => calculateCost(activeRatesFor(tier, inputTokens), inputTokens, outputTokens));
    const baselineCost = calculateCost(baselineTier.relativeRates, inputTokens, outputTokens);
    return tiers.map((tier, index) => ({
      ...tier,
      multiplier: costs[index] * (fastOnly ? tier.fastMode?.usageMultiplier ?? 1 : 1) / baselineCost,
      usesLongContext: tier.longContext !== null && inputTokens >= tier.longContext.atInputTokens
    })).filter(tier => !fastOnly || tier.fastMode !== null);
  };
  const calculateScaleMaximum = (tiers, baselineTier) => {
    const baselineRates = baselineTier.relativeRates;
    let maximumMultiplier = 1;
    tiers.forEach(tier => {
      const rateSets = [tier.relativeRates];
      if (tier.longContext !== null) {
        rateSets.push(tier.longContext.relativeRates);
      }
      rateSets.forEach(rates => {
        const fastestMultiplier = Math.max(1, tier.fastMode?.usageMultiplier ?? 1);
        maximumMultiplier = Math.max(maximumMultiplier, rates.input / baselineRates.input * fastestMultiplier, rates.output / baselineRates.output * fastestMultiplier);
      });
    });
    return maximumMultiplier;
  };
  const [inputTokens, setInputTokens] = useState(50000);
  const [outputTokens, setOutputTokens] = useState(8000);
  const [fastOnly, setFastOnly] = useState(false);
  const [requestVersion, setRequestVersion] = useState(0);
  const [request, setRequest] = useState({
    status: "loading",
    data: null
  });
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    setRequest({
      status: "loading",
      data: null
    });
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 10000);
    const apiOrigin = window.location.hostname === "docs.adam.new" ? "https://api.adam.new" : window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1" ? "http://localhost:3001" : "https://api.staging.adam.new";
    const usageTiersUrl = `${apiOrigin}/chat/usage-tiers`;
    fetch(usageTiersUrl, {
      headers: {
        Accept: "application/json"
      },
      signal: controller.signal
    }).then(response => {
      if (!response.ok) {
        throw new Error(`The server returned ${response.status}.`);
      }
      return response.json();
    }).then(value => normalizeResponse(value)).then(data => {
      if (active) {
        setRequest({
          status: "success",
          data
        });
      }
    }).catch(error => {
      if (active) {
        setRequest({
          status: "error",
          data: null,
          message: timedOut ? "The request timed out." : error.message
        });
      }
    }).finally(() => window.clearTimeout(timeout));
    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [requestVersion]);
  const retry = () => setRequestVersion(version => version + 1);
  if (request.status === "loading") {
    return <section className="usage-cost-comparison" aria-labelledby="usage-cost-comparison-title">
        <header className="usage-cost-header">
          <div>
            <h2 id="usage-cost-comparison-title">Compare usage costs</h2>
          </div>
        </header>
        <div className="usage-cost-loading" role="status" aria-live="polite">
          <span className="usage-cost-sr-only">Loading tier comparison</span>
          <div className="usage-cost-loading-line usage-cost-loading-line-short" aria-hidden="true" />
          <div className="usage-cost-loading-controls" aria-hidden="true" />
        </div>
      </section>;
  }
  if (request.status === "error" || request.data === null) {
    return <section className="usage-cost-comparison" aria-labelledby="usage-cost-comparison-title">
        <header className="usage-cost-header">
          <div>
            <h2 id="usage-cost-comparison-title">Compare usage costs</h2>
          </div>
        </header>
        <div className="usage-cost-error" role="alert">
          <div>
            <h3>Usage comparison temporarily unavailable</h3>
            <p>Try loading the comparison again.</p>
          </div>
          <button type="button" onClick={retry}>Try again</button>
        </div>
      </section>;
  }
  const tiers = request.data;
  const baselineTier = tiers.find(tier => tier.isBaseline);
  const tierNames = new Intl.ListFormat("en-US", {
    style: "long",
    type: "conjunction"
  }).format(tiers.map(tier => tier.label));
  const hasFastTiers = tiers.some(tier => tier.fastMode !== null);
  const estimates = estimateTiers(tiers, baselineTier, inputTokens, outputTokens, fastOnly && hasFastTiers);
  const scaleMaximum = calculateScaleMaximum(tiers, baselineTier);
  const announcement = `${fastOnly ? "Fast mode. " : "Standard mode. "}${estimates.map(tier => `${tier.label} ${formatMultiplier(tier.multiplier)} times ${baselineTier.label}`).join("; ")}`;
  return <section className="usage-cost-comparison" aria-labelledby="usage-cost-comparison-title">
      <p className="usage-cost-sr-only" role="status" aria-live="polite">
        {announcement}
      </p>

      <header className="usage-cost-header">
        <div>
          <h2 id="usage-cost-comparison-title">Compare usage costs</h2>
          <p className="usage-cost-intro">
            {tierNames} trade usage efficiency for capability. Move either slider to compare how much base usage the selected request consumes.
          </p>
        </div>
      </header>

      <div className="usage-cost-controls">
        <div className="usage-cost-sliders">
          <div className="usage-cost-slider-field">
            <div className="usage-cost-slider-heading">
              <label htmlFor="usage-cost-input-tokens">Context tokens</label>
              <output htmlFor="usage-cost-input-tokens">{formatTokens(inputTokens)}</output>
            </div>
            <p id="usage-cost-input-help" className="usage-cost-context-help">
              Assumes the context in this request is not cached.
            </p>
            <input id="usage-cost-input-tokens" type="range" min={INPUT_LIMITS.min} max={INPUT_LIMITS.max} step={INPUT_LIMITS.step} value={inputTokens} aria-describedby="usage-cost-input-help" aria-valuetext={`${formatTokens(inputTokens)} context tokens`} onChange={event => setInputTokens(Number(event.currentTarget.value))} />
            <div className="usage-cost-slider-limits" aria-hidden="true">
              <span>{formatCompactTokens(INPUT_LIMITS.min)}</span>
              <span>{formatCompactTokens(INPUT_LIMITS.max)}</span>
            </div>
          </div>

          <div className="usage-cost-slider-field">
            <div className="usage-cost-slider-heading">
              <label htmlFor="usage-cost-output-tokens">Output tokens</label>
              <output htmlFor="usage-cost-output-tokens">{formatTokens(outputTokens)}</output>
            </div>
            <p id="usage-cost-output-help">Adjust how much output the request generates.</p>
            <input id="usage-cost-output-tokens" type="range" min={OUTPUT_LIMITS.min} max={OUTPUT_LIMITS.max} step={OUTPUT_LIMITS.step} value={outputTokens} aria-describedby="usage-cost-output-help" aria-valuetext={`${formatTokens(outputTokens)} output tokens`} onChange={event => setOutputTokens(Number(event.currentTarget.value))} />
            <div className="usage-cost-slider-limits" aria-hidden="true">
              <span>{formatCompactTokens(OUTPUT_LIMITS.min)}</span>
              <span>{formatCompactTokens(OUTPUT_LIMITS.max)}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="usage-cost-current">
        <div className="usage-cost-results-heading">
          <div>
            <h3>Current comparison</h3>
            <p>Base usage: {baselineTier.label} below the large-context threshold.</p>
          </div>
          <div className="usage-cost-results-actions">
            {hasFastTiers ? <button type="button" className="usage-cost-fast-toggle" aria-controls="usage-cost-tier-results" aria-pressed={fastOnly} onClick={() => setFastOnly(enabled => !enabled)}>
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <path d="M13.2 2 4 13.2h7.1L10.4 22 20 9.8h-7.2L13.2 2Z" />
                </svg>
                <span>Fast</span>
                <span className="usage-cost-fast-switch" aria-hidden="true">
                  <span />
                </span>
              </button> : null}
          </div>
        </div>

        <ol id="usage-cost-tier-results" aria-label={`Tier usage relative to standard ${baselineTier.label}`}>
          {estimates.map(tier => {
    const width = Math.min(100, tier.multiplier / scaleMaximum * 100);
    return <li className="usage-cost-tier" key={tier.id}>
                <div className="usage-cost-tier-heading">
                  <div className="usage-cost-tier-name">
                    <div>
                      <div className="usage-cost-tier-title">
                        <h4>{tier.label}</h4>
                        {tier.usesLongContext ? <span className="usage-cost-context-badge">
                            Large context
                          </span> : null}
                        {fastOnly ? <span className="usage-cost-fast-badge">
                            <svg aria-hidden="true" viewBox="0 0 24 24">
                              <path d="M13.2 2 4 13.2h7.1L10.4 22 20 9.8h-7.2L13.2 2Z" />
                            </svg>
                            Fast
                          </span> : null}
                      </div>
                      {tier.description ? <p>{tier.description}</p> : null}
                    </div>
                  </div>
                  <div className="usage-cost-tier-total">
                    <strong>{formatMultiplier(tier.multiplier)}×</strong>
                    <span>
                      {tier.id === baselineTier.id && !tier.usesLongContext && !fastOnly ? "Base usage" : "of base usage"}
                    </span>
                  </div>
                </div>

                <div className="usage-cost-track" aria-hidden="true">
                  <span style={{
      width: `max(2px, ${width}%)`
    }} />
                </div>
              </li>;
  })}
        </ol>
      </div>
    </section>;
};

These are **task tiers**, not subscription plans. Your plan determines which tiers you can use and how much included usage you receive.

Choose a tier based on the work, not usage alone. Premium tiers use more of your allowance, but they are significantly more capable at 3D CAD modeling and complex engineering work. Reserve Lite for supporting tasks such as design reviews, straightforward edits, presentations, and bills of materials. Lite can handle limited 3D modeling, but use a premium tier when the CAD model itself is the main deliverable.

<UsageCostComparison />

**What is Fast mode?** Fast mode increases how quickly supported tiers generate their output, so you spend less time waiting for a response. It costs more to run, which means Fast requests consume more of your usage allowance than Standard requests. Use the Fast toggle above to see the difference.

**What is large context?** Large context applies when a request contains enough instructions, conversation history, or file content to cross the large-context threshold. Affected tiers use more of your allowance beyond that point, which is reflected automatically in the comparison above.

**Token pricing.** Adam does not mark up token usage. We charge it at our underlying cost and, in some cases, charge less than that cost.

The comparison shows uncached context and generated output for one request. Cached context can use less of your allowance. These are relative usage amounts, not subscription prices or separate charges.

Each request is deducted from the included usage provided by your plan. Max includes more weekly usage than Pro, and Team Max includes more usage per paid seat than Team Pro. On team plans, each assigned member uses the weekly allowance for their own Pro or Max seat. Add-on funds are shared across the workspace.

After your weekly allowance and the workspace's add-on funds are depleted, only Lite is available through a small daily allowance until paid usage becomes available again. See [Billing and usage](/admin/billing#after-weekly-usage-runs-out) for the full fallback behavior and refresh times.

