"""
APN Core Server - API Tests
Run with: pytest tests/ -v
"""
import pytest
import asyncio
from httpx import AsyncClient, ASGITransport

# Import the app
import sys
sys.path.insert(0, '..')
from apn_server import app


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
async def client():
    """Create async test client"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealthEndpoints:
    """Test health check endpoints"""

    @pytest.mark.anyio
    async def test_health_check(self, client):
        """Test basic health endpoint"""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["ok", "degraded"]
        assert "node_id" in data
        assert "version" in data

    @pytest.mark.anyio
    async def test_version_endpoint(self, client):
        """Test version endpoint"""
        response = await client.get("/api/version")
        assert response.status_code == 200
        data = response.json()
        assert "apn_core_version" in data
        assert "protocol_version" in data
        assert "node_id" in data


class TestPeerRegistration:
    """Test peer registration endpoints"""

    @pytest.mark.anyio
    async def test_register_peer_valid(self, client):
        """Test valid peer registration"""
        response = await client.post("/register", json={
            "nodeId": "test_node_001",
            "publicKey": "a" * 64,  # 32 bytes hex
            "roles": ["test"],
            "settings": {
                "capabilities": {"test": True}
            }
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "registered"
        assert "dashboard_node_id" in data

    @pytest.mark.anyio
    async def test_register_peer_invalid_key(self, client):
        """Test peer registration with invalid public key"""
        response = await client.post("/register", json={
            "nodeId": "test_node_002",
            "publicKey": "invalid_key",  # Too short
            "roles": []
        })
        assert response.status_code == 422  # Validation error


class TestTaskEndpoints:
    """Test task management endpoints"""

    @pytest.mark.anyio
    async def test_create_task(self, client):
        """Test task creation"""
        response = await client.post("/api/tasks", json={
            "title": "Test Task",
            "description": "A test task",
            "priority": "medium",
            "status": "pending"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "created"
        assert "task" in data
        assert data["task"]["title"] == "Test Task"

    @pytest.mark.anyio
    async def test_get_tasks(self, client):
        """Test getting tasks"""
        response = await client.get("/api/tasks")
        assert response.status_code == 200
        data = response.json()
        assert "tasks" in data
        assert "source" in data

    @pytest.mark.anyio
    async def test_create_task_invalid_priority(self, client):
        """Test task creation with invalid priority"""
        response = await client.post("/api/tasks", json={
            "title": "Test Task",
            "priority": "invalid_priority"
        })
        assert response.status_code == 422  # Validation error


class TestResourceEndpoints:
    """Test system resource endpoints"""

    @pytest.mark.anyio
    async def test_get_resources(self, client):
        """Test system resources endpoint"""
        response = await client.get("/api/resources")
        assert response.status_code == 200
        data = response.json()
        assert "node_id" in data
        assert "resources" in data
        assert "cpu" in data["resources"]
        assert "memory" in data["resources"]

    @pytest.mark.anyio
    async def test_contribution_status(self, client):
        """Test contribution status endpoint"""
        response = await client.get("/api/contribution/status")
        assert response.status_code == 200
        data = response.json()
        assert "node_id" in data
        assert "settings" in data
        assert "status" in data

    @pytest.mark.anyio
    async def test_update_contribution_settings(self, client):
        """Test updating contribution settings"""
        response = await client.post("/api/contribution/settings", json={
            "enabled": True,
            "relay": False,
            "compute": True,
            "storage": False,
            "storage_gb_allocated": 20,
            "compute_cores_allocated": 2,
            "bandwidth_limit_mbps": 50
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["settings"]["enabled"] is True


class TestMeshEndpoints:
    """Test mesh networking endpoints"""

    @pytest.mark.anyio
    async def test_get_mesh_peers(self, client):
        """Test getting mesh peers"""
        response = await client.get("/api/mesh/peers")
        assert response.status_code == 200
        data = response.json()
        assert "node_id" in data
        assert "peers" in data
        assert "known_peers" in data

    @pytest.mark.anyio
    async def test_mesh_message_no_route(self, client):
        """Test mesh message with no route"""
        response = await client.post("/api/mesh/message", json={
            "dest_node": "nonexistent_node",
            "payload": {"type": "ping"}
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "no_route"


class TestWearableEndpoints:
    """Test wearable endpoints"""

    @pytest.mark.anyio
    async def test_wearable_state(self, client):
        """Test wearable state update"""
        response = await client.post("/api/wearables/state", json={
            "ring_connected": True,
            "glasses_connected": False,
            "battery_level": 85
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"


class TestLandingPage:
    """Test landing page"""

    @pytest.mark.anyio
    async def test_landing_page(self, client):
        """Test landing page loads"""
        response = await client.get("/")
        assert response.status_code == 200
        assert "APN CORE" in response.text
        assert "Alpha Protocol Network" in response.text


class TestInputValidation:
    """Test input validation"""

    @pytest.mark.anyio
    async def test_task_title_too_long(self, client):
        """Test task title max length validation"""
        response = await client.post("/api/tasks", json={
            "title": "x" * 501,  # Max is 500
        })
        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_contribution_settings_bounds(self, client):
        """Test contribution settings bounds validation"""
        response = await client.post("/api/contribution/settings", json={
            "enabled": True,
            "storage_gb_allocated": -1,  # Must be >= 0
        })
        assert response.status_code == 422


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
