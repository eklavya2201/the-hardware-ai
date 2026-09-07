<!-- source: https://docs.adam.new/admin/billing.md fetched 2026-09-07T18:00:32+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Billing and usage

> Understand Adam plans, daily and weekly included usage, per-seat limits, add-on funds, and usage-limit behavior.

export const PlanComparison = () => {
  const PLAN_LABELS = {
    personal: {
      pro: "Pro",
      max: "Max"
    },
    team: {
      pro: "Team Pro",
      max: "Team Max"
    }
  };
  const FALLBACK_TAGLINES = {
    pro: "For regular individual use",
    max: "For heavier individual workloads",
    team: "For teams working together"
  };
  const formatPrice = priceCents => new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: priceCents % 100 === 0 ? 0 : 2,
    maximumFractionDigits: 2
  }).format(priceCents / 100);
  const formatMultiplier = value => new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 1
  }).format(value);
  const normalizePlanProducts = value => {
    if (!Array.isArray(value)) {
      throw new Error("The plan catalog is not valid.");
    }
    return value.filter(product => product !== null && typeof product === "object" && product.type === "subscription" && product.active === true && product.custom === false && (product.audience === "personal" || product.audience === "team") && (product.tierBand === "pro" || product.tierBand === "max") && (product.interval === "monthly" || product.interval === "annual") && Number.isFinite(product.priceCents) && product.priceCents >= 0 && typeof product.tierDefault === "boolean" && typeof product.weeklyUnlimited === "boolean" && (product.weeklyAllowanceCredits === null || Number.isFinite(product.weeklyAllowanceCredits) && product.weeklyAllowanceCredits >= 0));
  };
  const normalizeDisplay = value => {
    const tiers = value?.tiers;
    if (tiers === null || typeof tiers !== "object") {
      return FALLBACK_TAGLINES;
    }
    return {
      pro: typeof tiers.pro?.tagline === "string" && tiers.pro.tagline.trim() ? tiers.pro.tagline.trim() : FALLBACK_TAGLINES.pro,
      max: typeof tiers.max?.tagline === "string" && tiers.max.tagline.trim() ? tiers.max.tagline.trim() : FALLBACK_TAGLINES.max,
      team: typeof tiers.team?.tagline === "string" && tiers.team.tagline.trim() ? tiers.team.tagline.trim() : FALLBACK_TAGLINES.team
    };
  };
  const resolvePlan = (products, audience, tierBand) => {
    const candidates = products.filter(product => product.audience === audience && product.tierBand === tierBand);
    const monthly = candidates.filter(product => product.interval === "monthly");
    return (((monthly.find(product => product.tierDefault) ?? candidates.find(product => product.tierDefault)) ?? [...monthly].sort((a, b) => a.priceCents - b.priceCents)[0]) ?? [...candidates].sort((a, b) => a.priceCents - b.priceCents)[0]) ?? null;
  };
  const buildFamilies = (products, taglines) => {
    const familyDefinitions = [{
      audience: "personal",
      eyebrow: "Individual",
      title: "Personal plans",
      description: "One subscription for one person."
    }, {
      audience: "team",
      eyebrow: "Shared workspace",
      title: "Team plans",
      description: "Choose Pro or Max for each team member."
    }];
    return familyDefinitions.map(family => {
      const pro = resolvePlan(products, family.audience, "pro");
      const max = resolvePlan(products, family.audience, "max");
      if (pro === null || max === null) {
        const usageDetail = family.audience === "team" ? "for each assigned team member" : "for one person";
        return {
          ...family,
          plans: ["pro", "max"].map(tierBand => ({
            id: `${family.audience}-${tierBand}`,
            label: PLAN_LABELS[family.audience][tierBand],
            price: null,
            priceSuffix: null,
            tagline: family.audience === "team" ? taglines.team : taglines[tierBand],
            usageDetail,
            usageLead: tierBand === "pro" ? "Weekly included usage" : `More usage than ${PLAN_LABELS[family.audience].pro}`
          }))
        };
      }
      const baselineAllowance = pro.weeklyAllowanceCredits;
      const plans = [pro, max].map(product => {
        let usageLead = "Weekly included usage";
        let usageDetail = family.audience === "team" ? "for each assigned team member" : "for one person";
        if (product.weeklyUnlimited) {
          usageLead = "Unlimited usage";
          usageDetail = "included with this plan";
        } else if (product.tierBand === "max" && baselineAllowance !== null && baselineAllowance > 0 && product.weeklyAllowanceCredits !== null) {
          const multiple = product.weeklyAllowanceCredits / baselineAllowance;
          usageLead = `${formatMultiplier(multiple)}× ${PLAN_LABELS[family.audience].pro}`;
          usageDetail = family.audience === "team" ? "weekly usage for each assigned team member" : "weekly included usage";
        }
        return {
          id: `${family.audience}-${product.tierBand}`,
          label: PLAN_LABELS[family.audience][product.tierBand],
          price: formatPrice(product.priceCents),
          priceSuffix: product.interval === "annual" ? family.audience === "team" ? "per seat / year" : "per year" : family.audience === "team" ? "per seat / month" : "per month",
          tagline: family.audience === "team" ? taglines.team : taglines[product.tierBand],
          usageDetail,
          usageLead
        };
      });
      return {
        ...family,
        plans
      };
    });
  };
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
    const fetchJson = url => fetch(url, {
      headers: {
        Accept: "application/json"
      },
      signal: controller.signal
    }).then(response => {
      if (!response.ok) {
        throw new Error(`The server returned ${response.status}.`);
      }
      return response.json();
    });
    const apiOrigin = window.location.hostname === "docs.adam.new" ? "https://api.adam.new" : window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1" ? "http://localhost:3001" : "https://api.staging.adam.new";
    Promise.all([fetchJson(`${apiOrigin}/billing/products`), fetchJson(`${apiOrigin}/billing/pricing-display`).catch(() => null)]).then(([productsValue, displayValue]) => {
      const products = normalizePlanProducts(productsValue);
      const taglines = normalizeDisplay(displayValue);
      return buildFamilies(products, taglines);
    }).then(data => {
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
  if (request.status === "loading") {
    return <div className="plan-comparison plan-comparison-loading" role="status" aria-live="polite">
        <span className="plan-comparison-sr-only">Loading plan comparison</span>
        <div aria-hidden="true" />
        <div aria-hidden="true" />
      </div>;
  }
  if (request.status === "error" || request.data === null) {
    return <div className="plan-comparison plan-comparison-error" role="alert">
        <div>
          <strong>Plan comparison unavailable</strong>
          <p>You can still compare plans in Settings → Billing.</p>
        </div>
        <button type="button" onClick={() => setRequestVersion(version => version + 1)}>
          Try again
        </button>
      </div>;
  }
  return <div className="plan-comparison" aria-label="Paid plan comparison">
      <div className="plan-comparison-grid">
        {request.data.map(family => <section className="plan-comparison-family" key={family.audience}>
            <header>
              <span>{family.eyebrow}</span>
              <h3>{family.title}</h3>
              <p>{family.description}</p>
            </header>
            <div>
              {family.plans.map(plan => <article className="plan-comparison-plan" key={plan.id}>
                  <div className="plan-comparison-plan-heading">
                    <div>
                      <h4>{plan.label}</h4>
                      <p>{plan.tagline}</p>
                    </div>
                    {plan.price !== null ? <div className="plan-comparison-price">
                        <strong>{plan.price}</strong>
                        <span>{plan.priceSuffix}</span>
                      </div> : null}
                  </div>
                  <div className="plan-comparison-usage">
                    <strong>{plan.usageLead}</strong>
                    <span>{plan.usageDetail}</span>
                  </div>
                </article>)}
            </div>
          </section>)}
      </div>
    </div>;
};

Open your account menu, select **Settings**, and choose **Billing** to see your plan, usage, payment settings, and charge history.

The Billing page shows how much included usage remains and when it refreshes.

See [Usage costs by tier](/admin/usage-costs) to compare how Lite, Pro, and Ultra use your allowance.

## Compare plans

**Pro** and **Max** are individual plans. Both unlock Adam's paid task tiers and refresh included usage each week. Max is designed for heavier or sustained workloads and includes more weekly usage than Pro.

**Team Pro** and **Team Max** add a shared workspace, shared files and integrations, per-member usage visibility, and owner-managed roles. Team plans are billed per seat. Each assigned member uses the weekly allowance from their own Pro or Max seat, and Team Max provides more weekly usage per seat than Team Pro. Add-on funds are shared across the workspace.

<PlanComparison />

The **Free** plan includes a daily usage pool and access to Lite. **Enterprise** plans can be tailored for larger organizations that need custom usage, support, or administration.

## How usage refreshes

* Daily usage refreshes at **00:00 UTC**.
* Paid included usage refreshes each **Monday at 00:00 UTC**.
* On a team plan, paid weekly usage belongs to the member assigned to that seat. Daily usage is also tracked separately for each member in each workspace.
* Add-on funds are shared across the workspace and keep paid task tiers available after a member's weekly allowance is depleted.

Adam shows included usage as a percentage on the Billing page.

## After weekly usage runs out

When your paid weekly allowance and the workspace's add-on funds are depleted, Adam switches to a daily allowance for Lite:

* Pro and Ultra are unavailable for new messages in that workspace until paid usage becomes available again.
* You can start tasks and continue existing tasks with Lite using a small daily allowance at no extra cost.
* Your daily allowance refreshes each day and remains available until your weekly allowance resets.
* The Billing page shows Daily usage first, followed by your depleted Weekly usage and your actual Ultra usage for the week.

This fallback only changes model usage. You can still use other features included with your plan, such as projects, files, and integrations.

Adding funds makes Pro and Ultra available again. Otherwise, wait for your weekly allowance to refresh on Monday. If the daily allowance is also depleted, Lite becomes available again after the next daily refresh.

## Keep using Pro and Ultra

To restore paid model usage before the weekly refresh, you can:

* add funds to the workspace balance;
* turn on automatic reload; or
* upgrade to a plan with more weekly usage.

Otherwise, continue with Lite on your daily allowance or wait for the weekly allowance to refresh.

## Add-on funds and automatic reload

Add-on funds let you continue using Pro and Ultra after your weekly allowance runs out. Use **Add funds** on the Billing page to add money to the workspace balance.

Automatic reload adds a chosen amount when your balance falls below a threshold. Set a limit that matches your expected spending and review the charge history after changing it.

<Warning>
  Automatic reload creates real charges. Team owners and admins should review the payment method, threshold, amount, and any reload cap together.
</Warning>

## Manage the subscription

Use **Settings** → **Billing** to upgrade, change seats, add funds, manage automatic reload, review charges, or manage your subscription. On a shared team, the owner and admins manage billing.

For a charge you do not recognize, include the workspace name, date, and receipt or charge identifier when emailing [hello@adam.new](mailto:hello@adam.new). Do not send full card details.

