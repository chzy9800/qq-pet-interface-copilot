package dev.qqpets;

import java.io.ByteArrayOutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;

/**
 * Read-only proof of concept for QQ 9.3.25.
 *
 * This class has no Android/Xposed compile-time dependency. It must still be
 * invoked from inside the com.tencent.mobileqq process with QQ's ClassLoader.
 */
public final class QQPetDirectClient {
    public static final String GET_USER_PET_SSO =
            "trpc.qqone.gateway.Gateway.Sso_PetCache_GetUserPet";
    public static final String GET_STORY_STATUS_SSO =
            "trpc.qqone.gateway.Gateway.Sso_PetOutdoor_GetPetStoryStatus";

    private static final String DELEGATE_CLASS =
            "com.tencent.mobileqq.qqpet.delegate.l";
    private static final String OBSERVER_CLASS =
            "com.tencent.ergo.hostdelegate.pb.PetPbDelegate$a";

    private final ClassLoader qqClassLoader;
    private final Object delegate;
    private final Class<?> observerClass;
    private final Method sendSsoMethod;
    private final Method sendOidbMethod;

    public interface Callback {
        void onResult(int code, byte[] data, Object bundle);
    }

    public interface PetIdCallback {
        void onResult(int code, String petId, byte[] rawResponse, Object bundle);
    }

    public QQPetDirectClient(ClassLoader qqClassLoader) throws Exception {
        this.qqClassLoader = qqClassLoader;
        Class<?> delegateClass = Class.forName(DELEGATE_CLASS, true, qqClassLoader);
        this.observerClass = Class.forName(OBSERVER_CLASS, true, qqClassLoader);

        Constructor<?> constructor = delegateClass.getDeclaredConstructor();
        constructor.setAccessible(true);
        this.delegate = constructor.newInstance();

        this.sendSsoMethod = delegateClass.getMethod(
                "a", byte[].class, String.class, observerClass);
        this.sendOidbMethod = delegateClass.getMethod(
                "c", byte[].class, String.class, int.class, int.class, observerClass);
    }

    private void sendSso(String command, byte[] request, Callback callback) throws Exception {
        sendSsoMethod.invoke(delegate, request, command, newObserver(callback));
    }

    private void sendOidb(
            String commandName,
            int command,
            int subCommand,
            byte[] request,
            Callback callback) throws Exception {
        sendOidbMethod.invoke(
                delegate,
                request,
                commandName,
                command,
                subCommand,
                newObserver(callback));
    }

    /**
     * QQ sends an empty GetUserPet request when no cached petId exists. The
     * response is GetUserPetRsp { pet = 1 }, and Pet.petId is field 101.
     */
    public void queryOwnPet(PetIdCallback callback) throws Exception {
        sendOidb("OidbSvcTrpcTcp.0x95e1_0", 38369, 0, new byte[0],
                (code, data, bundle) -> {
                    String petId = null;
                    if (code == 0 && data != null) {
                        try {
                            byte[] pet = ProtoWire.firstBytes(data, 1);
                            petId = pet == null ? null : ProtoWire.firstString(pet, 101);
                        } catch (RuntimeException ignored) {
                            // Return the raw response so the caller can retain evidence.
                        }
                    }
                    callback.onResult(code, petId, data, bundle);
                });
    }

    /** Read-only status query for active/finished work or study stories. */
    public void queryStoryStatus(String petId, Callback callback) throws Exception {
        byte[] request = ProtoWire.message().string(1, petId).toByteArray();
        sendOidb("OidbSvcTrpcTcp.0x975a_1", 38746, 1, request, callback);
    }

    private Object newObserver(Callback callback) {
        return Proxy.newProxyInstance(
                qqClassLoader,
                new Class<?>[]{observerClass},
                (proxy, method, args) -> {
                    if ("onResult".equals(method.getName())) {
                        int code = ((Number) args[0]).intValue();
                        callback.onResult(code, (byte[]) args[1], args[2]);
                        return null;
                    }
                    if ("toString".equals(method.getName())) {
                        return "QQPetDirectObserver";
                    }
                    if ("hashCode".equals(method.getName())) {
                        return System.identityHashCode(proxy);
                    }
                    if ("equals".equals(method.getName())) {
                        return proxy == args[0];
                    }
                    return null;
                });
    }

    /** Minimal protobuf wire codec; deliberately limited to this probe. */
    public static final class ProtoWire {
        private final ByteArrayOutputStream out = new ByteArrayOutputStream();

        public static ProtoWire message() {
            return new ProtoWire();
        }

        public ProtoWire string(int field, String value) {
            if (value == null || value.isEmpty()) {
                return this;
            }
            byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
            writeVarint(out, ((long) field << 3) | 2L);
            writeVarint(out, bytes.length);
            out.write(bytes, 0, bytes.length);
            return this;
        }

        public byte[] toByteArray() {
            return out.toByteArray();
        }

        public static String firstString(byte[] message, int wantedField) {
            byte[] value = firstBytes(message, wantedField);
            return value == null ? null : new String(value, StandardCharsets.UTF_8);
        }

        public static byte[] firstBytes(byte[] message, int wantedField) {
            int[] position = {0};
            while (position[0] < message.length) {
                long tag = readVarint(message, position);
                int field = (int) (tag >>> 3);
                int wireType = (int) (tag & 7);
                if (wireType == 2) {
                    int length = checkedLength(readVarint(message, position), message, position[0]);
                    int start = position[0];
                    position[0] += length;
                    if (field == wantedField) {
                        byte[] result = new byte[length];
                        System.arraycopy(message, start, result, 0, length);
                        return result;
                    }
                } else {
                    skipValue(message, position, wireType);
                }
            }
            return null;
        }

        private static void skipValue(byte[] data, int[] position, int wireType) {
            switch (wireType) {
                case 0:
                    readVarint(data, position);
                    return;
                case 1:
                    advance(data, position, 8);
                    return;
                case 2:
                    int length = checkedLength(readVarint(data, position), data, position[0]);
                    advance(data, position, length);
                    return;
                case 5:
                    advance(data, position, 4);
                    return;
                default:
                    throw new IllegalArgumentException("Unsupported protobuf wire type " + wireType);
            }
        }

        private static void advance(byte[] data, int[] position, int count) {
            if (count < 0 || position[0] > data.length - count) {
                throw new IllegalArgumentException("Truncated protobuf value");
            }
            position[0] += count;
        }

        private static int checkedLength(long length, byte[] data, int position) {
            if (length < 0 || length > Integer.MAX_VALUE || length > data.length - position) {
                throw new IllegalArgumentException("Invalid protobuf length " + length);
            }
            return (int) length;
        }

        private static long readVarint(byte[] data, int[] position) {
            long result = 0;
            for (int shift = 0; shift < 64; shift += 7) {
                if (position[0] >= data.length) {
                    throw new IllegalArgumentException("Truncated protobuf varint");
                }
                int b = data[position[0]++] & 0xff;
                result |= (long) (b & 0x7f) << shift;
                if ((b & 0x80) == 0) {
                    return result;
                }
            }
            throw new IllegalArgumentException("Malformed protobuf varint");
        }

        private static void writeVarint(ByteArrayOutputStream out, long value) {
            while ((value & ~0x7fL) != 0) {
                out.write(((int) value & 0x7f) | 0x80);
                value >>>= 7;
            }
            out.write((int) value);
        }
    }
}
