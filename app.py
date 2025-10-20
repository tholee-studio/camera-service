import os
import time
import threading
import tempfile
from flask import Flask, Response, jsonify, send_file, request
from flask_cors import CORS
import gphoto2 as gp
from waitress import serve
import logging
import av
from PIL import Image

HOST = "0.0.0.0"
PORT = 2461
SIMULATION = False

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s %(levelname)s: %(message)s"
)

app = Flask(__name__)
app.config.update(
    DEBUG=True,
)
CORS(app)

# Global variables for stream control
fps = 30
frame_time = 1.0 / fps

stream_active = False
camera_lock = threading.Lock()
camera = None


def init_camera():
    global camera
    with camera_lock:
        if camera is None:
            try:
                camera = gp.Camera()
                camera.init()
                print("Camera initialized")
                return True
            except gp.GPhoto2Error as ex:
                print(f"Camera init error: {ex}")
                camera = None
                return False
        return True


def release_camera():
    global camera
    with camera_lock:
        if camera is not None:
            try:
                camera.exit()
                print("Camera released")
            except gp.GPhoto2Error as ex:
                print(f"Camera exit error: {ex}")
            finally:
                camera = None


def generate_frames():
    global stream_active
    while stream_active:
        with camera_lock:
            if camera is None:
                break
            try:
                camera_file = gp.check_result(gp.gp_camera_capture_preview(camera))
                file_data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + file_data + b"\r\n"
                )
            except gp.GPhoto2Error as ex:
                print(f"Stream error: {ex}")
                break
        time.sleep(frame_time)


@app.route("/liveview/start", methods=["GET"])
def start_stream():
    if SIMULATION:
        return jsonify({"status": "Stream started"})

    global stream_active
    if not init_camera():
        return jsonify({"error": "Camera initialization failed"}), 500

    if not stream_active:
        stream_active = True
        return jsonify({"status": "Stream started"})
    else:
        return jsonify({"status": "Stream already running"})


@app.route("/liveview/stop", methods=["GET"])
def stop_stream():
    if SIMULATION:
        return jsonify({"status": "Stream stopped"})

    global stream_active
    stream_active = False
    release_camera()
    return jsonify({"status": "Stream stopped"})


@app.route("/")
def status():
    return jsonify({"ok": "true", "message": "camera-service is running"}), 200


@app.route("/liveview")
def liveview():
    if SIMULATION:
        filename = f"samples/liveview_simulation.jpg"
        return send_file(filename, mimetype="image/jpeg")

    if not stream_active:
        return jsonify({"error": "Stream not active. Call /stream/start first"}), 400
    return Response(
        generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/capture", methods=["GET"])
def capture():
    if SIMULATION:
        filename = f"samples/capture_simulation.jpg"
        return send_file(filename, mimetype="image/jpeg")

    if not init_camera():
        return jsonify({"error": "Camera not available"}), 503

    try:
        with camera_lock:
            file_path = gp.check_result(
                gp.gp_camera_capture(camera, gp.GP_CAPTURE_IMAGE)
            )
            camera_file = gp.check_result(
                gp.gp_camera_file_get(
                    camera, file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL
                )
            )

            local_path = f"capture.jpg"
            gp.check_result(gp.gp_file_save(camera_file, local_path))

            gp.check_result(
                gp.gp_camera_file_delete(camera, file_path.folder, file_path.name)
            )

            return send_file(local_path, mimetype="image/jpeg")

    except gp.GPhoto2Error as ex:
        release_camera()
        return jsonify({"error": str(ex)}), 500
    except Exception as ex:
        release_camera()
        return jsonify({"error": str(ex)}), 500


@app.route("/exposure/options", methods=["GET"])
def get_exposure_options():
    """Get available options for ISO, Shutter Speed, Aperture"""
    if not init_camera():
        return jsonify({"error": "Camera not available"}), 503

    try:
        with camera_lock:
            config = camera.get_config()
            iso_options = [
                str(choice) for choice in config.get_child_by_name("iso").get_choices()
            ]
            shutter_options = [
                str(choice)
                for choice in config.get_child_by_name("shutterspeed").get_choices()
            ]
            aperture_options = [
                str(choice)
                for choice in config.get_child_by_name("aperture").get_choices()
            ]

            return jsonify(
                {
                    "iso": iso_options,
                    "shutter": shutter_options,
                    "aperture": aperture_options,
                }
            )

    except gp.GPhoto2Error as ex:
        return jsonify({"error": str(ex)}), 500
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/exposure", methods=["GET"])
def get_exposure():
    """Get current exposure settings"""
    if not init_camera():
        return jsonify({"error": "Camera not available"}), 503
    try:
        with camera_lock:
            config = camera.get_config()
            iso = config.get_child_by_name("iso").get_value()
            shutter = config.get_child_by_name("shutterspeed").get_value()
            aperture = config.get_child_by_name("aperture").get_value()
            return jsonify({"iso": iso, "shutter": shutter, "aperture": aperture})
    except gp.GPhoto2Error as ex:
        return jsonify({"error": str(ex)}), 500
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/exposure", methods=["POST"])
def set_exposure_params():
    """Set exposure settings via query params"""
    if not init_camera():
        return jsonify({"error": "Camera not available"}), 503

    iso = request.args.get("iso")
    shutter = request.args.get("shutter")
    aperture = request.args.get("aperture")

    try:
        with camera_lock:
            config = camera.get_config()

            if iso:
                try:
                    config.get_child_by_name("iso").set_value(str(iso))
                except gp.GPhoto2Error:
                    return jsonify({"error": f"Invalid ISO value: {iso}"}), 400

            if shutter:
                try:
                    config.get_child_by_name("shutterspeed").set_value(str(shutter))
                except gp.GPhoto2Error:
                    return jsonify({"error": f"Invalid Shutter value: {shutter}"}), 400

            if aperture:
                try:
                    config.get_child_by_name("aperture").set_value(str(aperture))
                except gp.GPhoto2Error:
                    return (
                        jsonify({"error": f"Invalid Aperture value: {aperture}"}),
                        400,
                    )

            camera.set_config(config)

        return jsonify({"status": "ok"}), 200

    except gp.GPhoto2Error as ex:
        return jsonify({"error": str(ex)}), 500
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/video", methods=["POST"])
def video():
    files = request.files.getlist("photos[]")
    
    if not files:
        return {"error": "No images uploaded"}, 400

    orientation = request.form.get("orientation") or request.args.get("orientation")
    if orientation == "landscape":
        output_width = 1080
        output_height = 720
    else:
        output_width = 720
        output_height = 1080

    tmp_dir = tempfile.mkdtemp()
    image_paths = []
    for i, file in enumerate(files):
        filename = f"{i:04d}.jpg"
        path = os.path.join(tmp_dir, filename)
        file.save(path)
        image_paths.append(path)

    output_path = os.path.join(tmp_dir, "video.mp4")

    # parameter
    fps_input = 3  # kecepatan pergantian gambar (video logic)
    fps_output = 30  # fps video final
    total_duration = 15

    total_frames = total_duration * fps_input

    # ambil ukuran dari gambar pertama
    first_img = Image.open(image_paths[0]).convert("RGB")
    width, height = first_img.size

    # buka container untuk output
    container = av.open(output_path, mode="w")
    stream = container.add_stream("libx264", rate=fps)
    stream.pix_fmt = "yuv420p"
    stream.width = output_width
    stream.height = output_height

    stream.options = {
        "preset": "superfast",
    }

    # generate frame sesuai total durasi
    for i in range(total_frames):
        img_path = image_paths[i % len(image_paths)]  # loop
        img = Image.open(img_path).convert("RGB")

        frame = av.VideoFrame.from_image(img)

        # repeat frame agar match fps_output
        for _ in range(fps_output // fps_input):
            for packet in stream.encode(frame):
                container.mux(packet)

    # flush sisa frame encoder
    for packet in stream.encode():
        container.mux(packet)

    container.close()

    return send_file(output_path, as_attachment=True, download_name="video.mp4")


if __name__ == "__main__":
    # app.run(host=HOST, port=PORT, threaded=True)

    try:
        serve(app, host=HOST, port=PORT, threads=4)
        # app.run(host='0.0.0.0', port=2461, threaded=True)
    finally:
        stream_active = False
        release_camera()
