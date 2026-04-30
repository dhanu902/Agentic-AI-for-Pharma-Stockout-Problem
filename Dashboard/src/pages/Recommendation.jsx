import React, { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

const API_BASE = "/api/recommendation";

export default function RecommendationPage() {
  const [searchParams] = useSearchParams();
  const urlSku = searchParams.get("sku") || "";

  const [sku, setSku] = useState(urlSku);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchRecommendation = async (itemCode = sku) => {
    if (!itemCode) {
      setError("Please enter SKU.");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const res = await fetch(`${API_BASE}/dashboard`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_code: itemCode }),
      });

      const result = await res.json();

      if (!res.ok || !result.ok) {
        throw new Error(result.error || "Failed to load recommendation.");
      }

      setData(result.data);
    } catch (err) {
      setError(err.message || "Failed to load recommendation.");
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (urlSku) {
      setSku(urlSku);
      fetchRecommendation(urlSku);
    }
  }, [urlSku]);

  const risk = data?.risk_summary || {};
  const rec = data?.recommendation || {};
  const flags = data?.business_flags || [];
  const explanation = data?.explanation || [];
  const horizon = data?.horizon || [];

  return (
    <div style={{ padding: 24 }}>
      <h1>Recommendation Page</h1>

      <div style={{ marginBottom: 20 }}>
        <input
          value={sku}
          onChange={(e) => setSku(e.target.value)}
          placeholder="Enter ItemCode"
          style={{ padding: 8, marginRight: 8 }}
        />

        <button onClick={() => fetchRecommendation(sku)} disabled={loading}>
          {loading ? "Loading..." : "Get Recommendation"}
        </button>

        <button
          onClick={() => {
            if (sku) window.location.href = `/risk?sku=${sku}`;
          }}
          style={{ marginLeft: 8 }}
        >
          Back to Risk
        </button>
      </div>

      {error && (
        <div style={{ color: "red", marginBottom: 16 }}>
          {error}
        </div>
      )}

      {!data && !loading && (
        <p>No recommendation loaded.</p>
      )}

      {data && (
        <>
          <section>
            <h2>Risk Summary</h2>
            <p><b>ItemCode:</b> {data.item_code}</p>
            <p><b>Risk Level:</b> {risk.risk_level}</p>
            <p><b>Forecast Qty:</b> {risk.forecast_qty}</p>
            <p><b>Unmet Qty:</b> {risk.unmet_qty}</p>
            <p><b>Closing Stock:</b> {risk.closing_stock}</p>
            <p><b>Incoming Qty:</b> {risk.incoming_qty}</p>
          </section>

          <hr />

          <section>
            <h2>Recommendation</h2>
            <p><b>Action Type:</b> {rec.action_type}</p>
            <p><b>Recommended Qty:</b> {rec.recommended_qty}</p>
            <p><b>Priority:</b> {rec.priority}</p>
            <p><b>Needs Approval:</b> {rec.needs_approval ? "Yes" : "No"}</p>
          </section>

          <hr />

          <section>
            <h2>Business Flags</h2>

            {flags.length === 0 ? (
              <p>No flags.</p>
            ) : (
              flags.map((flag, index) => (
                <div key={index} style={{ border: "1px solid #ccc", padding: 8, marginBottom: 8 }}>
                  <p><b>Type:</b> {flag.type}</p>
                  <p><b>Status:</b> {flag.status}</p>
                  <p><b>Message:</b> {flag.message}</p>
                </div>
              ))
            )}
          </section>

          <hr />

          <section>
            <h2>Explanation</h2>

            {explanation.length === 0 ? (
              <p>No explanation available.</p>
            ) : (
              <ul>
                {explanation.map((line, index) => (
                  <li key={index}>{line}</li>
                ))}
              </ul>
            )}
          </section>

          <hr />

          <section>
            <h2>Horizon Rows</h2>

            {horizon.length === 0 ? (
              <p>No horizon data available.</p>
            ) : (
              <table border="1" cellPadding="6">
                <thead>
                  <tr>
                    <th>Horizon</th>
                    <th>Forecast Month</th>
                    <th>Risk Level</th>
                    <th>Forecast Qty</th>
                    <th>Incoming Qty</th>
                    <th>Closing Stock</th>
                    <th>Unmet Qty</th>
                  </tr>
                </thead>
                <tbody>
                  {horizon.map((row, index) => (
                    <tr key={index}>
                      <td>{row.Horizon}</td>
                      <td>{row.Forecast_Month}</td>
                      <td>{row.Risk_Level}</td>
                      <td>{row.Forecast_Qty}</td>
                      <td>{row.Incoming_Qty}</td>
                      <td>{row.Closing_Total_Stock}</td>
                      <td>{row.Unmet_Qty}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </div>
  );
}