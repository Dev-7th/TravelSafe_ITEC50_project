const fuelDataScript = document.getElementById("fuel-data-json");
const fuelChartCanvas = document.getElementById("fuelPriceChart");

if (fuelDataScript && fuelChartCanvas) {
  const rawData = JSON.parse(fuelDataScript.textContent);

  rawData.sort((a, b) => new Date(a.Date) - new Date(b.Date));
  
  const labels = rawData.map((row) => row.Date);
  const brentPrices = rawData.map((row) => Number(row.Brent_USD));
  const wtiPrices = rawData.map((row) => Number(row.WTI_USD));

  new Chart(fuelChartCanvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Brent Crude (USD)",
          data: brentPrices,
          borderColor: "#1f77b4",
          backgroundColor: "rgba(31, 119, 180, 0.15)",
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
          tension: 0.2
        },
        {
          label: "WTI Crude (USD)",
          data: wtiPrices,
          borderColor: "#d62728",
          backgroundColor: "rgba(214, 39, 40, 0.15)",
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
          tension: 0.2
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: "index",
        intersect: false
      },
      plugins: {
        tooltip: {
          callbacks: {
            label(context) {
              return `${context.dataset.label}: $${context.parsed.y.toFixed(2)}`;
            }
          }
        }
      },
      scales: {
        x: {
          title: {
            display: true,
            text: "Date"
          }
        },
        y: {
          title: {
            display: true,
            text: "Price (USD)"
          },
          ticks: {
            callback(value) {
              return `$${value}`;
            }
          }
        }
      }
    }
  });
}
