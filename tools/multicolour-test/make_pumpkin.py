# Two-colour pumpkin test print for the K2 CFS: orange ribbed body, green
# stem and leaf. Writes body.npz / green.npz (vertices + triangles, mm).
import numpy as np

R0, HC, ZC = 27.5, 22.0, 14.0      # body: equator radius, half height, centre z
LOBES, GROOVE = 8, 0.08            # rib count and groove depth (fraction of R)
DIMPLE, DIMPLE_R = 6.0, 10.0       # top dimple depth and radius


def rib(theta):
    return 1.0 - GROOVE * (1.0 - np.abs(np.cos(LOBES * theta / 2.0))) ** 1.5


def z_top(rho, theta):
    """Height of the body's upper surface at radius rho, angle theta."""
    g = rib(theta)
    c = np.clip(rho / (R0 * g), 0.0, 1.0)
    r_unmod = rho / g
    return ZC + HC * np.sqrt(1.0 - c * c) - DIMPLE * np.exp(-(r_unmod / DIMPLE_R) ** 2)


def body(n_theta=192, n_t=96):
    t_b = np.arcsin(-ZC / HC)                       # flat base at z = 0
    t = np.linspace(t_b, np.pi / 2, n_t)
    r = R0 * np.cos(t)
    # dimple only on the upper half, so the flat base stays at z = 0
    z = ZC + HC * np.sin(t) - DIMPLE * np.exp(-(r / DIMPLE_R) ** 2) * (t > 0)
    z[0] = 0.0
    th = np.linspace(0, 2 * np.pi, n_theta, endpoint=False)
    g = rib(th)
    # rings 0..n_t-2 (the last profile point is the top pole)
    verts = [(0.0, 0.0, 0.0)]                        # bottom centre
    for i in range(n_t - 1):
        for j in range(n_theta):
            verts.append((r[i] * g[j] * np.cos(th[j]), r[i] * g[j] * np.sin(th[j]), z[i]))
    verts.append((0.0, 0.0, z[-1]))                  # top pole
    V = np.array(verts)
    F = []
    ring = lambda i, j: 1 + i * n_theta + (j % n_theta)
    for j in range(n_theta):                         # base disk
        F.append((0, ring(0, j + 1), ring(0, j)))
    for i in range(n_t - 2):
        for j in range(n_theta):
            a, b = ring(i, j), ring(i, j + 1)
            c, d = ring(i + 1, j), ring(i + 1, j + 1)
            F += [(a, b, d), (a, d, c)]
    top = len(V) - 1
    for j in range(n_theta):
        F.append((ring(n_t - 2, j), ring(n_t - 2, j + 1), top))
    return V, np.array(F)


def tube(path, radii, ridges=6, ridge_depth=0.12, n_phi=36, twist=0.6):
    """Closed ridged tube along a 3D path (stem)."""
    path = np.asarray(path)
    n = len(path)
    V, F = [], []
    for i in range(n):
        tng = path[min(i + 1, n - 1)] - path[max(i - 1, 0)]
        tng /= np.linalg.norm(tng)
        a = np.array([1.0, 0, 0]) if abs(tng[0]) < 0.9 else np.array([0, 1.0, 0])
        u = np.cross(tng, a); u /= np.linalg.norm(u)
        v = np.cross(tng, u)
        for k in range(n_phi):
            phi = 2 * np.pi * k / n_phi + twist * i / n
            rr = radii[i] * (1 + ridge_depth * np.cos(ridges * phi))
            V.append(path[i] + rr * (np.cos(phi) * u + np.sin(phi) * v))
    V.append(path[0]); V.append(path[-1])
    c0, c1 = len(V) - 2, len(V) - 1
    idx = lambda i, k: i * n_phi + (k % n_phi)
    for i in range(n - 1):
        for k in range(n_phi):
            a, b, c, d = idx(i, k), idx(i, k + 1), idx(i + 1, k), idx(i + 1, k + 1)
            F += [(a, b, d), (a, d, c)]
    for k in range(n_phi):
        F.append((c0, idx(0, k + 1), idx(0, k)))
        F.append((c1, idx(n - 1, k), idx(n - 1, k + 1)))
    return np.array(V), np.array(F)


def leaf(theta0=0.0, rho0=4.5, rho1=21.0, width=7.0, thick=1.2, embed=0.4,
         nu=60, nv=12):
    """A leaf lying on the body's top surface, pointing out along theta0."""
    u = np.linspace(0, 1, nu)
    v = np.linspace(-1, 1, nv)
    half = np.maximum(width / 2 * np.sin(np.pi * np.clip(u, 0.04, 0.98)) ** 0.8, 0.35)
    rho = rho0 + (rho1 - rho0) * u
    bend = 0.25 * np.sin(np.pi * u)                  # gentle sideways curve
    cx = rho * np.cos(theta0 + bend * (1 - u))
    cy = rho * np.sin(theta0 + bend * (1 - u))
    d = np.stack([np.gradient(cx), np.gradient(cy)], 1)
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    perp = np.stack([-d[:, 1], d[:, 0]], 1)
    X = cx[:, None] + perp[:, 0, None] * half[:, None] * v[None, :]
    Y = cy[:, None] + perp[:, 1, None] * half[:, None] * v[None, :]
    rr = np.hypot(X, Y)
    tt = np.arctan2(Y, X)
    zs = z_top(rr, tt)
    # thinner at the edges, a raised midrib in the middle
    t_local = thick * (0.55 + 0.45 * (1 - np.abs(v)) ** 2)[None, :] * np.ones_like(X)
    Ztop = zs + t_local
    Zbot = zs - embed
    top = np.stack([X, Y, Ztop], -1).reshape(-1, 3)
    bot = np.stack([X, Y, Zbot], -1).reshape(-1, 3)
    V = np.vstack([top, bot])
    N = nu * nv
    idx = lambda i, j: i * nv + j
    F = []
    for i in range(nu - 1):
        for j in range(nv - 1):
            a, b, c, d2 = idx(i, j), idx(i, j + 1), idx(i + 1, j), idx(i + 1, j + 1)
            F += [(a, c, d2), (a, d2, b)]                         # top, up
            F += [(N + a, N + b, N + d2), (N + a, N + d2, N + c)]  # bottom, down
    for i in range(nu - 1):                                       # long edges
        for j, flip in ((0, False), (nv - 1, True)):
            a, c = idx(i, j), idx(i + 1, j)
            q = [(a, N + a, N + c), (a, N + c, c)]
            F += [tuple(reversed(t)) for t in q] if flip else q
    for i, flip in ((0, True), (nu - 1, False)):                  # ends
        for j in range(nv - 1):
            a, b = idx(i, j), idx(i, j + 1)
            q = [(a, b, N + b), (a, N + b, N + a)]
            F += [tuple(reversed(t)) for t in q] if flip else q
    return V, np.array(F)


def merge(*parts):
    Vs, Fs, off = [], [], 0
    for V, F in parts:
        Vs.append(V); Fs.append(F + off); off += len(V)
    return np.vstack(Vs), np.vstack(Fs)


if __name__ == "__main__":
    Vb, Fb = body()
    z0 = ZC + HC - DIMPLE                 # dimple floor
    s = np.linspace(0, 1, 28)
    path = np.stack([2.5 * s ** 2, 0.8 * s ** 2, z0 - 0.8 + 15.5 * s], 1)
    radii = 4.6 - 1.4 * s + 0.5 * np.exp(-((s - 0.0) / 0.08) ** 2)
    Vs, Fs = tube(path, radii)
    Vl, Fl = leaf(theta0=np.radians(-35))
    Vg, Fg = merge((Vs, Fs), (Vl, Fl))
    np.savez("body.npz", V=Vb, F=Fb)
    np.savez("green.npz", V=Vg, F=Fg)
    for name, V in (("body", Vb), ("green", Vg)):
        print(name, len(V), "verts, bbox", V.min(0).round(2), V.max(0).round(2))
