/**
 * CostGuardian Dashboard JavaScript
 * Fetches data from S3 and renders charts/tables
 */

// Global variables
let dashboardData = null;
let pieChart = null;
let lineChart = null;

// Base URL for S3 bucket (update this after deployment)
const DATA_URL = 'data.json';

/**
 * Initialize dashboard on page load
 */
document.addEventListener('DOMContentLoaded', function() {
    console.log('🚀 CostGuardian Dashboard loading...');
    loadDashboardData();
});

/**
 * Load dashboard data from S3
 */
async function loadDashboardData() {
    try {
        const response = await fetch(DATA_URL);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        dashboardData = await response.json();
        console.log('✅ Data loaded successfully', dashboardData);
        
        // Hide loading, show content
        document.getElementById('loadingState').style.display = 'none';
        document.getElementById('mainContent').style.display = 'block';
        
        // Render all components
        renderMetrics();
        renderServiceBreakdown();
        renderCharts();
        renderResourcesTable();
        renderLastUpdated();
        populateMonthSelect();
        
    } catch (error) {
        console.error('❌ Error loading data:', error);
        showError(error.message);
    }
}

/**
 * Render metric cards
 */
function renderMetrics() {
    const current = dashboardData.current_month;
    
    document.getElementById('totalSavings').textContent = 
        formatCurrency(current.total_savings);
    
    document.getElementById('totalResources').textContent = 
        current.total_resources.toLocaleString();
    
    document.getElementById('cumulativeSavings').textContent = 
        formatCurrency(dashboardData.cumulative_savings);
}

/**
 * Render service breakdown cards
 */
function renderServiceBreakdown() {
    const container = document.getElementById('serviceBreakdown');
    const services = dashboardData.current_month.savings_by_service;
    
    if (services.length === 0) {
        container.innerHTML = '<p style="color: #999; text-align: center; padding: 20px;">No resources deleted this month</p>';
        return;
    }
    
    container.innerHTML = services.map(service => `
        <div class="service-breakdown">
            <div class="service-icon">${getServiceIcon(service.service_code)}</div>
            <div class="service-details">
                <div class="service-name">${service.service}</div>
                <div class="service-count">${service.count} resource${service.count !== 1 ? 's' : ''} deleted</div>
            </div>
            <div class="service-savings">${formatCurrency(service.savings)}</div>
        </div>
    `).join('');
}

/**
 * Get icon for service type
 */
function getServiceIcon(serviceCode) {
    const icons = {
        'EC2': '🖥️',
        'RDS': '🗄️',
        'NAT_GATEWAY': '🌐',
        'ALB': '⚖️',
        'NLB': '⚖️',
        'ELB': '⚖️',
        'EBS': '💿',
        'VPC': '🔒',
        'S3': '📦'
    };
    return icons[serviceCode] || '☁️';
}

/**
 * Render charts
 */
function renderCharts() {
    renderPieChart();
    renderLineChart();
}

/**
 * Render pie chart for service distribution
 */
function renderPieChart() {
    const ctx = document.getElementById('pieChart').getContext('2d');
    const breakdown = dashboardData.breakdown;
    
    if (breakdown.length === 0) {
        ctx.font = '16px Arial';
        ctx.fillStyle = '#999';
        ctx.textAlign = 'center';
        ctx.fillText('No data available', ctx.canvas.width / 2, ctx.canvas.height / 2);
        return;
    }
    
    const colors = [
        '#667eea', '#10b981', '#f59e0b', '#ef4444', 
        '#8b5cf6', '#ec4899', '#14b8a6', '#f97316'
    ];
    
    pieChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: breakdown.map(item => item.name),
            datasets: [{
                data: breakdown.map(item => item.value),
                backgroundColor: colors.slice(0, breakdown.length),
                borderWidth: 2,
                borderColor: '#fff'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        padding: 15,
                        font: {
                            size: 12
                        }
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const label = context.label || '';
                            const value = formatCurrency(context.parsed);
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const percentage = ((context.parsed / total) * 100).toFixed(1);
                            return `${label}: ${value} (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });
}

/**
 * Render line chart for historical data
 */
function renderLineChart() {
    const ctx = document.getElementById('lineChart').getContext('2d');
    const historical = dashboardData.historical;
    
    lineChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: historical.map(item => item.month_name),
            datasets: [{
                label: 'Monthly Savings',
                data: historical.map(item => item.savings),
                borderColor: '#667eea',
                backgroundColor: 'rgba(102, 126, 234, 0.1)',
                borderWidth: 3,
                fill: true,
                tension: 0.4,
                pointRadius: 5,
                pointHoverRadius: 7,
                pointBackgroundColor: '#667eea',
                pointBorderColor: '#fff',
                pointBorderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return `Savings: ${formatCurrency(context.parsed.y)}`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        callback: function(value) {
                            return '$' + value.toFixed(0);
                        }
                    },
                    grid: {
                        color: 'rgba(0, 0, 0, 0.05)'
                    }
                },
                x: {
                    grid: {
                        display: false
                    }
                }
            }
        }
    });
}

/**
 * Render resources table
 */
function renderResourcesTable() {
    const tbody = document.getElementById('resourcesTableBody');
    const resources = dashboardData.resources_detail;
    
    if (resources.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #999; padding: 30px;">No resources deleted this month</td></tr>';
        return;
    }
    
    // Sort by date (newest first)
    resources.sort((a, b) => new Date(b.date) - new Date(a.date));
    
    tbody.innerHTML = resources.map(resource => `
        <tr>
            <td>${formatDate(resource.date)}</td>
            <td><code>${resource.resource_id}</code></td>
            <td>${resource.resource_type}</td>
            <td>${resource.instance_type}</td>
            <td style="color: #10b981; font-weight: 600;">${formatCurrency(resource.monthly_savings)}</td>
        </tr>
    `).join('');
}

/**
 * Render last updated timestamp
 */
function renderLastUpdated() {
    const timestamp = new Date(dashboardData.last_updated);
    document.getElementById('lastUpdated').textContent = 
        `Last updated: ${timestamp.toLocaleString()}`;
}

/**
 * Populate month selector with available historical reports
 */
function populateMonthSelect() {
    const select = document.getElementById('monthSelect');
    const historical = dashboardData.historical;
    
    // Add historical months (excluding current month)
    historical.slice(0, -1).forEach(month => {
        const option = document.createElement('option');
        option.value = month.month;
        option.textContent = month.month_name;
        select.appendChild(option);
    });
}

/**
 * Download current month data as JSON
 */
function downloadCurrentJSON() {
    const dataStr = JSON.stringify(dashboardData, null, 2);
    const dataBlob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(dataBlob);
    
    const link = document.createElement('a');
    link.href = url;
    link.download = `costguardian-${dashboardData.current_month.month}.json`;
    link.click();
    
    URL.revokeObjectURL(url);
}

/**
 * Download current month data as CSV
 */
function downloadCurrentCSV() {
    const resources = dashboardData.resources_detail;
    
    // CSV header
    let csv = 'Date,Resource ID,Resource Type,Instance Type,Monthly Savings\n';
    
    // CSV rows
    resources.forEach(resource => {
        csv += `${resource.date},${resource.resource_id},${resource.resource_type},${resource.instance_type},$${resource.monthly_savings.toFixed(2)}\n`;
    });
    
    // Summary
    csv += `\nTotal Savings,,,$${dashboardData.current_month.total_savings.toFixed(2)}\n`;
    csv += `Total Resources,,,${dashboardData.current_month.total_resources}\n`;
    
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    
    const link = document.createElement('a');
    link.href = url;
    link.download = `costguardian-${dashboardData.current_month.month}.csv`;
    link.click();
    
    URL.revokeObjectURL(url);
}

/**
 * Download historical report
 */
function downloadHistoricalReport() {
    const select = document.getElementById('monthSelect');
    const month = select.value;
    
    if (!month) {
        alert('Please select a month');
        return;
    }
    
    // Open the historical report URL in new tab
    window.open(`reports/${month}.csv`, '_blank');
}

/**
 * Format currency
 */
function formatCurrency(amount) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2
    }).format(amount);
}

/**
 * Format date
 */
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric'
    });
}

/**
 * Show error message
 */
function showError(message) {
    document.getElementById('loadingState').style.display = 'none';
    document.getElementById('errorState').style.display = 'block';
    document.getElementById('errorMessage').textContent = message;
}

/**
 * Refresh dashboard data
 */
function refreshDashboard() {
    document.getElementById('mainContent').style.display = 'none';
    document.getElementById('loadingState').style.display = 'block';
    loadDashboardData();
}

// Auto-refresh every 5 minutes
setInterval(refreshDashboard, 5 * 60 * 1000);
