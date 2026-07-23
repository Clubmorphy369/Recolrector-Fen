import base64

def process_image_bytes(image_bytes):
    """Envía la imagen a la API de Chessvision.ai y devuelve el FEN."""
    try:
        # Codificar la imagen a base64
        encoded_string = base64.b64encode(image_bytes).decode('utf-8')
        
        # Construir el payload para la API de Chessvision.ai
        payload = {
            "board_orientation": "predict",
            "cropped": False,
            "current_player": "white",  # Puedes intentar predecirlo o pedir input al usuario
            "image": f"data:image/jpeg;base64,{encoded_string}",
            "predict_turn": True
        }

        # Realizar la solicitud POST
        response = requests.post(
            'http://app.chessvision.ai/predict',
            json=payload,
            timeout=30
        )

        print(f"[DEBUG] Chessvision.ai status: {response.status_code}")
        print(f"[DEBUG] Chessvision.ai response: {response.text[:300]}")

        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return data.get('result')
            else:
                return f"Error de Chessvision.ai: {data.get('message', 'Error desconocido')}"
        else:
            return f"Error HTTP {response.status_code}: {response.text[:100]}"

    except requests.exceptions.Timeout:
        return "Error: Tiempo de espera agotado"
    except Exception as e:
        return f"Error: {str(e)}"
