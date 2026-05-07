
async function loadNCRAverages() {
    try {
        const response = await fetch('/api/ncr-averages');

        if (!response.ok) {
            throw new Error("NCR average request failed.");
        }

        const data = await response.json();
        const displayDiv = document.getElementById('ncr-display');

        if (!displayDiv) {
            return;
        }

        if (!data.length) {
            displayDiv.innerHTML = '<span class="summary-empty">No NCR average data available.</span>';
            return;
        }

        displayDiv.innerHTML = data.map(item => `
            <div class="market-summary-item">
                <span class="summary-label">NCR ${item.fuel_type}</span>
                <strong>PHP ${Number(item.average_price).toFixed(2)}</strong>
            </div>
        `).join('');

    } catch (error) {
        console.error("NCR averages could not be loaded:", error);
    }
}

loadNCRAverages();
