import { StyleSheet, Text, View } from 'react-native';
import { RadioTower } from 'lucide-react-native';
import { Theme } from '../../constants/theme';

/** RASTA's mark: the tower in brand blue beside the name, as on the web console. */
export function BrandMark() {
  return (
    <View style={styles.row} accessible accessibilityLabel="RASTA">
      <RadioTower size={22} color={Theme.colors.brand} />
      <Text style={styles.name}>RASTA</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  name: {
    fontSize: 20,
    fontWeight: '800',
    color: Theme.colors.text,
    letterSpacing: -0.2,
  },
});
